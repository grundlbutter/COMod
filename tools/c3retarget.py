#!/usr/bin/env python3
r"""c3retarget.py -- a glTF (Mixamo) clip, retargeted onto a C3 body track.

    py -3 tools/c3retarget.py --gltf <scene.gltf> --root <install> --shape 3 \
        --target c3/0003/000/003.c3 --name gangnam-dance3-v1

Writes ``coroot.export_dir()/rig/<name>/<target>`` plus ``manifest.json``,
the way `tools/c3rig.py apply` does, and NEVER into an install: the owner
installs the one file with comod (`docs/c3retarget_2026-09-30.md`).

WHAT THE ENGINE ACCEPTS, PROVEN 2026-09-30 ON THE LIVE OFFICIAL CLIENT
-----------------------------------------------------------------------
Re-baked RAW body tracks placed loose play in every animation; the three
1-bone socket tracks regenerated as constant offsets of their host bones
follow; and `c3rig`'s pivot bug -- a rotation about the model origin
instead of the joint -- is exactly what an "elongated arm" looks like. So
the whole job is: produce 84 ABSOLUTE skinning matrices per frame whose
rotations follow the donor and whose translations keep every joint
connected, in the file's own encoding and byte length.

THE MATRIX CONVENTION, AND HOW IT IS PROVED RATHER THAN ASSUMED
----------------------------------------------------------------
A MOTI body track is bone_count x frame_count x 16 floats: one 4x4 per
bone per key in ROW-VECTOR form, ``posed = [x y z 1] . M``, translation
in row 3 (floats 12..14). `bonerig._as4` reads the 16 floats as that
matrix's rows and `c3rig._flat16` writes them back.

Everything in this module that REASONS about a rotation does so in the
column-vector form ``p' = R . p`` -- the glTF reader's convention, and the
one the rest-offset and axis-change algebra reads naturally in. The one
place the two meet is `col3_to_m16`: the 3x3 stored in the MOTI is the
TRANSPOSE of the column-form rotation, because ``v . M = (M^T . v)^T``.
That is not asserted from the prose. `tests/test_c3retarget.py` proves it
two ways: `effects.quat_to_matrix` -- which is `graphic.dll!Motion_Load`'s
own D3DXMatrixRotationQuaternion in row-vector form, i.e. the definition
-- must equal `col3_to_m16(quat_to_mat3(q))` for a spread of quaternions,
and a synthetic track built through `solve_translations` must keep its
joints coincident under `xf_row` (the same row-vector application
`bonerig` verified the rig's joint points with, 0.000 on the originals),
where the transposed storage MUST NOT.

CONNECTIVITY IS SOLVED, NOT INHERITED (the pivot lesson)
---------------------------------------------------------
The donor supplies rotations only. Translations come from the rig's
joint points: for bone ``b`` with rig parent ``p`` and shared joint ``J``
(the point `bonejoint` found both bones carry to the same place in every
frame),

    t_b = J . S_p + t_p - J . S_b            (S = the stored 3x3, row form)

so ``J`` lands in the same place under both matrices whatever ``S_b`` is.
The pelvis, a root with no joint, turns about its own bind centroid:
``t_1 = P1 - P1 . S_1 + delta_root``. A bone with a rig parent and no
donor mapping is RIGID with its parent (same matrix), and a bone with
neither parent nor mapping is CARRIED from the original clip at the same
frame index -- the original is a valid clip, so its matrices for bones no
skinned body uses are as good as any.

MEASURED ON THE TARGET, 2026-09-30 (`CCO-snapshot-2026-08-24`)
-----------------------------------------------------------------
* `c3/0003/000/003.c3`: 2,851,824 bytes, sha 3d4f647bd78698d7; MOTI at
  chunk indices 2/4/6 (1-bone sockets) and 8 (84 bones), all RAW,
  frame_count 501, 501 keys, no extra channels. RAW carries one 64-byte
  matrix per bone per frame, so same counts = same byte length.
* Frame 0 of THIS clip is a dance pose, not identity: max deviation from
  identity over the 84 bones is 249.5. (The "frame 0 ~ identity" rule holds
  for the idles; a retargeter must not lean on it, and this one does not --
  bind = identity is a property of the SKIN, not of any frame.)
* The family rig solved at the default budget (74.3 s, 286 clips, 3,000
  frames) has the 28 parents `docs/bone_labels_2026-09-21.md` names; 26
  joints; sockets 0->6 (`v_armet` on the head), 1->11 (`v_l_weapon` on
  the +x hand: the character's LEFT) and 2->20 (`v_r_weapon` on the -x
  thumb bone: the RIGHT). PHY order in the container is v_armet,
  v_l_weapon, v_r_weapon, v_body and the PHY<->MOTI binding is positional
  (docs/modding.md 11.1), so the socket ORDINAL names the side; the
  bonelabel suffixes (`hand.R` for 11) are mirrored (backlog 43, PR #204).
* The owner's garment `c3/mesh/003188495.c3` skins 25 bones INCLUDING toes
  45 and 50, which the reference mesh (`c3/0003/000/001.c3`'s `v_body`,
  551 vertices) does not, so the rig has no parent for them. Left as
  "carried" they would perform the ORIGINAL dance while the feet perform
  the new one -- detached toes. `--attach 45:44,50:49` (every shape's
  default, `DEFAULT_ATTACH`) makes them rigid with the feet the labels put
  them under. An attach applies to any bone the rig leaves WITHOUT a parent
  -- absent from `rig.parents`, or keyed there with None, as the pre-#200
  rig keys the flap 65 -- and one the rig does parent is refused OUT LOUD:
  a warning and the manifest's `attachRefused`, never a silent skip. The
  default is PER SHAPE (`DEFAULT_ATTACH_BY_SHAPE`, `default_attach`) since
  2026-10-02: shape 2's `v_armet` socket (the hat) rides bone 7, which its
  reference body skins nothing on, so the rig had no place for 7, the
  retarget CARRIED it from vanilla and the hat sat 67-108 units from the
  dancing head; vanilla's bone 7 IS the head's matrix (1.5e-05 over 2,375
  keys), so shape 2 adds `7:6`. A socket host the rig gives no place is
  `socket_hosts_outside_rig`; the attach it needs is DERIVED from the
  family's clips by `socket_host_outside_rig` (the table is that check's
  answer, and the check runs at export when the table misses one) and one
  left carried is a WARNING and `manifest.socketHosts.unattached`. The
  same garment is where the FEET get their direction (`--garment`, default
  that file): its toe centroids sit at (+-20.66, -15.20, -1.86), so the
  c3 foot direction is ankle joint -> toe centroid, the segment the donor's
  `Foot -> ToeBase` names. The first build used ankle -> FOOT centroid
  (0.084, -0.705, 0.704), 45 deg below horizontal against the donor's 33,
  and read the 12 deg gap as a "rest offset": applied, it pitched both feet
  ~12 deg toes-up in every frame. Ankle -> toe: 3.77 / 3.97 deg. Direction
  centroids come from the garment where it skins the bone, else from the
  reference mesh, and the manifest says which (`directionCentroidSource`);
  the garment skins 12, 14 and 22 too (not 20), so `--fingers` moves
  those three centroids off the reference mesh's.
* The pelvis direction is `mid(hip joints 1-42, 1-47) -> spine joint 1-2`,
  the mirror of the donor's `Spine - mid(UpLegs)`. The first build averaged
  EVERY non-spine child joint of bone 1, which on p84 includes the two
  skirt-flap joints 1-52 (y +8.0) and 1-64 (y -11.36); they dragged the
  midpoint to y -0.84 and the pelvis (and flaps 52/53/64, rigid with it)
  carried an 11.13 deg forward pitch in all 501 frames. Hips-only: 0.48.
* Bones 12/14 are the +x hand's thumb and fingers by geometry (reference
  mesh: 12 sits forward (-Y) and up of the hand 11's centroid by (3.71,
  -6.67, 5.16) with mass 4; 14 further out along +X by (10.2, -0.1, 0.82)
  with mass 19), and 20/22 are their EXACT mirrors on -x (12 (78.35,
  -4.12, -135.50) vs 20 (-78.35, -4.12, -135.50); 14 (84.85, 2.45,
  -139.84) vs 22 (-84.85, 2.45, -139.84)). The garment agrees where it
  skins them (14 (84.09, 2.00, -139.41) vs 22 (-83.95, 1.86, -139.31); it
  skins 12 but not 20). +x being the LEFT, 12 = the left thumb and 20 =
  the right one, which is where `v_r_weapon` (socket 2) rides.

MEASURED ON THE DONOR (Sketchfab glTF 2.0, `gangnam_style_converted`)
----------------------------------------------------------------------
50 nodes, 1 skin of 42 joints (41 `mixamorig:<Name>_<nn>` plus Sketchfab's
static `_rootJoint` at the origin), one LINEAR animation of 64 channels
(30 translation, 34 rotation) over 12.367 s. Only the Hips translation
channel VARIES -- per-axis range 1.332 / 0.167 / 0.223 in the node's local
units (x / y / z), 0.513 on the norm; the other 29 have zero range (< 1e-9)
and are constants, so a bone's world direction is its joint's world rotation
applied to a constant local offset. The root chain (`Sketchfab_model`
+90 deg about X at scale 189.06, then the `.fbx` node -90 deg at 0.01) is
a net uniform scale of 1.8906 with identity rotation: the skeleton is
Y-up in world, Hips at y 1.747, Head 2.945, LeftHand x +1.298 and
RightHand x -1.298 (+x is the donor's left, as in every Mixamo rig). The mesh
is 1.81 units tall in its own space and is skinned through inverse bind
matrices, so any scale is taken from the SKELETON's rest heights.

THE AXIS MAP -- AND THE SIDE DECISION IT RESTS ON
--------------------------------------------------
c3 = A . gltf with ``A = ((1,0,0),(0,0,-1),(0,-1,0))``: c3.X = +x, c3.Y =
-z, c3.Z = -y. det(A) = -1, and it has to be: glTF is right-handed, C3
is left-handed with +Z down (`c3anim.AXIS`, docs/modding.md 9.7), and
the map that keeps the character's chirality across a handedness change
is improper. Conjugating a rotation by it, ``D' = A . D . A^T``, is still
a proper rotation (det(A)^2 = 1): the same turn seen from inside the
left-handed frame, and ``A . (D . v) = D' . (A . v)`` for every v.

+x IS THE CHARACTER'S LEFT. Decided 2026-09-30 by the owner and two
independent measurements, none of them a label:

 (a) The game's own binding. PHY order in every shape-3 container is
     v_armet, v_l_weapon, v_r_weapon, v_body, and the PHY<->MOTI binding
     is POSITIONAL (docs/modding.md 11.1: MeshCreate RVA 0x28360 /
     MotionCreate 0x28470, 792/792 containers). Socket ordinal 1 is
     therefore `v_l_weapon`, and the rig says it rides bone 11 -- the
     hand on the +x chain (joint 10-11 at x +71.3). Ordinal 2,
     `v_r_weapon`, rides bone 20 on -x. (2026-10-01: that ordinal is a
     property of the SHAPE, not of the game -- shape 2's containers put
     `v_body` FIRST, so its `v_l_weapon` is ordinal 2, and on shape 4 the
     socket rides THUMB 12, a child rigid with hand 11. The tool now reads
     the ordinal off the target's PHY NAMES, `socket_ordinal`, and the
     side check accepts a finger the rig hangs off the mapped hand,
     `mapping_ok`. The constants above are shape 3's, measured.)
 (b) The owner's Phase-0 v3 screenshots: bone 9 turned 180 deg lifted the
     BOW (socket 1). Facing the camera the raised arm is on the screen's
     right; from behind, on the screen's left. The character's LEFT arm.
 (c) The handedness: a chirality-preserving glTF -> C3 map has det -1,
     and only a det -1 map puts the donor's LeftHand (rest x +1.298) on
     +x without turning every rotation the other way.

So `core/bonelabel.SIDE = {1: ".R"}` is MIRRORED (backlog item 43, PR
#204), and both earlier exports -- `gangnam-dance3-v1` and `-v2`, built
with ``((-1,0,0),(0,0,-1),(0,-1,0))`` (det +1) and the donor's RightHand
on bone 11 -- are mirror images of the donor; the second pass's geometry
verifier failed the build on exactly this. That map is kept as
`MIRROR_AXIS_MAP` and that mapping as `mirrored_mapping()`, for the
CONTROL below and nothing else.

THE PROBE IS DEGENERATE, AND WHAT DECIDES IT INSTEAD. The rest-pose probe
(`docs/c3retarget_2026-09-30.md` section 4) scores a candidate map by the
angle between each c3 bind bone direction and the mapped donor rest
direction, and it CANNOT tell the shipped composition from the v1/v2
one: a mirrored, Left <-> Right swapped T-pose is the same set of
directions, and both score mean 6.62 (v3 directions; 6.90 with v2's,
8.41 with v1's). Nor does the residual on the OUTPUT decide anything
(`conventionResidual`, once "fidelity"): under `--rest-offset direction`
it is ``R_b d_c = D'.C^-1 d_c = A.(donor posed dir)`` -- an algebraic
identity, 0.0 under either map, an axis sign flip, a swapped arm mapping
or a wrong pivot alike. It measures that the storage convention is
self-consistent, and nothing else. The decision is the `anatomy` block
of the manifest, each check asserted by `tests/test_c3retarget.py` on
the real clip -- and each ALTERNATIVE composition run there too, so the
attribution below is measured, not argued:

* ``det(A) = -1``.
* SIDE, keyed on the game: `left_hand_bone(rig, ordinal)` reads the
  `v_l_weapon` socket's host (11 on shape 3; the ordinal from the
  target's PHY names, `socket_ordinal`) off `rig.socket_constants`; the
  donor's LeftHand must be MAPPED to it -- or to the hand the rig hangs
  it off, when the host is a finger (`mapping_ok`) -- and the donor
  LeftHand's REST position mapped through A (+1.298) must have the sign
  of that bone's centroid x (+76.9 on the garment; the reference mesh's
  is +74.6).
* MOTION: over the frames written, the posed bone-11 centroid's x
  relative to the posed pelvis pivot and the donor LeftHand's x relative
  to Hips, mapped, agree in sign in > `MOTION_CUT` = 75 % of frames
  (measured 487/501 = 0.972 with `--tail loop`, 495/501 = 0.988 with
  `hold`; the disagreements are hand-crossing frames where both are near
  0). The cut was 0.9 until 2026-10-01, calibrated on shape 3 alone; it
  false-failed shape 1's 004.c3 at 0.880 (the constant's comment has the
  table).
* FRONT: the donor's ``Foot -> ToeBase`` mapped through A within 45 deg
  of the c3 foot -> toe direction (3.8 / 4.0). Front is -Y in c3 and +z
  in glTF; the probe's second-best map ``(+gx, +gz, -gy)`` flips it.

    composition                          det  worst offset  SIDE geometry      SIDE mapping  MOTION
    shipped   A' det -1, LeftHand -> 11   -1   13.67         agree +1.3/+76.9   ok            0.972
    mirror alone  A det +1, Left -> 11    +1*  174.5*        FAIL* -1.3/+76.9   ok            0.956 (blind)
    swap alone    A' det -1, Right -> 11  -1   174.1*        agree (blind)      FAIL*         0.431*
    v1/v2     A det +1, RightHand -> 11   +1*  13.66         FAIL* -1.3/+76.9   FAIL*         0.515*

  (* = the check fires.) SIDE's geometry rejects every det +1 map
  whatever the mapping; MOTION and the mapping check reject every
  mapping with the donor's RightHand on 11 whatever the map; the two
  half-way compositions also show as 170 deg rest offsets on the arms,
  and v1/v2 does NOT -- it is the composition only SIDE (and det) can
  see, and the one that shipped twice. The second pass had this
  attribution wrong: its RIGHT check was said not to see mirror+swap (it
  did) and to see swap-alone (it was blind; MOTION caught that).

THE REST OFFSET -- WHERE THE PROSE WAS WRONG AND THE TEST DECIDED
------------------------------------------------------------------
The two rigs' T-poses differ by a few degrees per bone. Let ``C_b`` be the
shortest rotation taking the donor's (A-converted) rest bone direction
onto the c3 bind direction. The task's draft applied the donor's world
delta ``D'`` by CONJUGATION, ``C . D' . C^-1``: that rotates the c3 bone by
the donor's angle about a re-expressed axis, so the c3 bone's direction
ends up at ``C . (donor posed direction)`` -- the whole rest offset stays
in the result, every frame; the largest offsets shipped are RightHand
13.67 (bone 19), Spine2 13.29 and LeftHand 12.96 deg (`restOffset.worst`
in the manifest names the worst bone). The form that makes the c3 bone point where the donor bone
points is

    R_b = D' . C_b^-1

(turn the c3 bone onto the donor's rest direction first, then apply the
donor's world delta), which is `--rest-offset direction`, the default.
The draft's form is kept as `--rest-offset conjugate` so the difference
can be measured rather than argued; `none` applies ``D'`` bare. The
`conventionResidual` table in the manifest -- per mapped bone, the angle
between the c3 posed bone direction and the donor's -- reads the measured
6.62 deg T-pose difference under `none`, 5.93 under `conjugate` and 0.0
under `direction` (6.90 / 6.21 with the second build's centroid head;
8.4 / 7.7 with the first build's pelvis and foot directions); under
`direction` that 0.0 is an IDENTITY (see THE AXIS MAP above), so the
table is kept as the storage-convention check it is and the `anatomy`
block carries the falsifiers.

Bone DIRECTIONS on both sides are joint-to-joint (the rig's joint points
on the c3 side, the child joint's world position on the donor's), not
centroid-to-centroid: a centroid sits in the middle of a bone's mass, so
shin->foot by centroids points at the middle of the foot rather than at
the ankle and reads as a 20 deg "rest difference" that is really a proxy
error. A bone whose direction child has no rig joint (the feet -> toes)
uses its head joint -> the child's centroid. A leaf whose donor
counterpart is a FRAME AXIS (the head: `Head -> HeadTop_End` leans 6.35
deg forward of the donor's own up at rest) uses the frame axis on BOTH
sides (`C3_FRAME_AXIS` / `DONOR_FRAME_AXIS`, up), offset 0.00 by
construction: its centroid sat 11.9 deg forward of the 5-6 joint and,
measured as a segment, pitched the head 5.56 deg back in every frame --
the feet's trap again -- and the c3 skull top has no stable cut (the mean
of the highest 5 / 10 / 20 % of head vertices reads 0.8 / 9.1 / 7.3 deg
forward on the garment, 3.0 / 3.7 / 6.8 on the reference mesh; the
donor's 6.35 is inside that spread, so the rest difference is not
measurable from the mesh and both rigs stand upright). The hands are
joint-to-joint on both sides and their 12.96 / 13.67 is GENUINE T-pose
difference, not proxy: the donor's wrist bends 10.8 deg up at rest
against the c3's 2.8, and its index knuckle lies within 0.3 / 1.2 deg of
yaw of its own forearm line, so the Hand -> HandIndex1 proxy costs about
a degree. Any other leaf (the fingers, under `--fingers`) uses its head
joint -> own centroid. Centroids come from the garment where it skins
the bone, else from the reference mesh.

WHAT IT WILL NOT DO
-------------------
* Twist. A direction fixes two of a bone's three degrees of freedom; the
  roll of the donor's frame about its own bone relative to the c3 bone's
  frame is unknowable from directions and is taken as zero. Forearm
  pronation and hand roll therefore carry the donor's roll relative to
  the donor's T-pose, which is the right quantity only if the two T-poses
  roll alike.
* Change the clip's frame count or encoding UNLESS ASKED (`--frames`,
  below). Without it the client reads the source's `frame_count`; 501
  frames at `--fps-ms` 41 (INFERRED, docs/animation.md section 5; the 33
  ms rival is a flag away) is 20.5 s against a 12.4 s donor, so the tail
  is `--tail loop` (restart the dance) or `hold`.
* Fingers beyond the first phalanx, or toes as anything but rigid.

MONSTERS AND NPCs (2026-10-01; `tools/rigtarget.py`, `tools/reanimate_npcs.py`)
--------------------------------------------------------------------------------
The same pipeline on a rig that is not p84, with four things made
parametric where they were shape 3's constants:

* THE TARGET. `--shape <3 digits>` is a monster shape: `ini/3dmotion.ini`'s
  9-digit rows map it to its `c3/monster/<dir>/` family (colour morphs share
  the directory and the skeleton; 59 ship on CCO), the idle is
  ``<dir>/100.c3``, the drawn mesh is `3dobj.ini`'s ``<body>000000`` row.
  `--npc <geometry id | npc.json type | name>` is an NPC family through
  `core/npcart`: every row drawing one geometry, its standby/rest/blaze
  files. The family rig is solved from THOSE clips with the DRAWN mesh as
  the reference body (a motion-only family has no body in any clip; an
  NPC standby's PHY is a 3-vertex stub) and cached as
  ``out/indexes/<base-id>/rig/<family>.json``.
* THE BODY ORDINAL is the CONTAINER's: the MOTI with the most bones
  (`body_slot_of`), as `bonerig.family_files` decides it. A rig's
  `body_ordinal` (3 on shape 3) describes the containers it was solved
  from; NPC 901 Sage's standby holds the same 84-bone body at ordinal 0
  with no sockets, and the monsters all bind at 0. Sockets are regenerated
  for the ordinals the container HAS and the rig solved a host for; the
  rest are carried byte-for-byte and listed (`socketsCarried`).
* THE TABLES (`--map auto`, `RigTables`, `derive_tables`): MAPPING, the
  fingers, the direction children, the frame axis, the hip children, the
  attach, the hover bones and the pelvis -- every table this module
  hard-codes for p84 -- derived from `core/bonelabel.label_tree` labels of
  the loaded rig. THE ORACLE: on the p84 rig the derived tables EQUAL the
  hand-written ones field for field and the export is byte-identical
  (2aeab67c3828fc27 on the fresh master solve), `tests/test_c3retarget_npcs.py`.
  Two rules the p84 constants never needed: a hand with no finger child
  takes its FOREARM LINE as its rest direction (its centroid is a fist's:
  Guard 900 read 36 and 43 deg, asymmetric; the donor's own
  `Hand -> HandIndex1` is within 1.2 deg of its forearm), and a headless
  neck is the top of the spine and takes the head's frame-axis rule (126's
  centroid read 94 deg: a forward-jutting head, kept as posed).
* THE HUMANOID GATE (`humanoid_verdict`): a target missing a core label the
  mapping needs (16: pelvis, chest, the seven limb names each side; neck
  and head optional -- 126 has no head) or carrying a DUPLICATE (a tail
  labelled as a third leg: 103, 200, 205) or an extra chain (`head_2..`:
  152, 155, 203) is REFUSED by name, never a traceback. p84's own skirt
  flaps are labelled `thigh.R`/`shin.R` three times over by `label_tree`
  ("every child of the pelvis is a leg"); a leg REACHES A FOOT, a pelvis
  chain that does not is a flap (`clean_labels`), so the player's rig
  passes its own gate.
* THE ANATOMY GATE (`anatomy_gate`, 2026-10-02), run when the labels pass.
  Labels are TOPOLOGY and monster 161 passed them 17/18 while being no
  biped: its labelled thighs are horizontal limbs, its real legs were
  demoted to a flap, a skinned bone had no parent, and the build floated
  58-109 units off the ground. Three measurements, each a refusal naming
  its numbers: a labelled thigh's bind direction more than
  `THIGH_DOWN_CUT_DEG` (45) from DOWN (161: 78.8 / 80.1; the humanoids
  5.5-24.2); a bone the drawn mesh SKINS left without a parent or an
  attach (161's bone 25, weight 12.0 -- it would be carried from the
  source clip); a pelvis that is not a root with exactly two leg chains.
* THE SIDE on a rig with no weapon socket (46 of 59 monster families, every
  NPC measured) cannot be keyed on the game: +x = the character's LEFT is
  CARRIED from the player, `anatomy.side.carried` says so, MOTION is
  measured on the mapped left hand, and the owner decides by eye.
* `--frames N` writes N dense keys at `--fps-ms` whatever the source holds
  (302 = one donor loop; 0 of 846 monster clips reach 302 frames, so the
  player route's "long target" trick has no analogue). PROVED 2026-10-01 by
  the owner on the live client: Guard 900's 20-frame RAW standby resampled
  to 302 played one ~12 s cycle -- the engine honours `frame_count`, on a
  RAW -> RAW body. The source encoding is kept where `effects.serialize_moti`
  writes it bit-exact at the new count (KKEY, XKEY: `encode_body`), else
  RAW with the source named; a carried bone is the source stretched by the
  engine's own lerp; every non-body chunk -- PHY, CAME, the sockets left
  alone -- is carried byte-for-byte. Anything but RAW -> RAW at a new count
  is an UNVERIFIED engine acceptance and the manifest's `engineVerified`
  says so per file.
* `--ground` (2026-10-02; OFF by default, so every player-route build
  keeps its bytes): a vertical root term from the per-frame hover gap, so
  the feet keep the donor's clearance. The `direction` rest offset
  straightens a crouched bind and the straightened leg is LONGER: monster
  129 (knees bent 55 deg) sank 6-23 units, 126 up to 8. `GROUNDS` has the
  three modes and their measurements; the builder passes `smooth`.
* A PLAYER-BODIED NPC (`--npc Sage`; `rigtarget.player_body`,
  `borrowed_route`, 2026-10-02) runs the PLAYER route on the player's rig:
  84 bones, and shape 3's joints stay connected under its own clips (mean
  residual 0.003 against 0.85 / 3.26 for shapes 2 / 4; own-shape clips
  read 0.0007-0.0103). Its own 73 frames give a rig that agrees with the
  player's on 7 of 26 parents. The player's reference body supplies the
  pelvis pivot and the root scale (Sage's robe puts its own pelvis
  centroid at 54.1 against 106.6), its drawn mesh is the garment, and the
  21 bones it skins that the player rig does not place FOLLOW a host
  (`derive_followers`): their source motion relative to that host is kept,
  never their absolute source matrices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bonerig                                               # noqa: E402
import bonetree                                              # noqa: E402
import c3anim                                                # noqa: E402
import c3phy                                                 # noqa: E402
import c3rig                                                 # noqa: E402
import c3write                                               # noqa: E402
import coassets                                              # noqa: E402
import coroot                                                # noqa: E402
import effects                                               # noqa: E402
import gltfread                                              # noqa: E402
import bonelabel                                             # noqa: E402
import rigtarget                                             # noqa: E402

MOTI_TAG = b"MOTI"

#: c3 = AXIS_MAP . gltf  (column-vector 3x3): c3.X = +x, c3.Y = -z, c3.Z = -y.
#: det -1 -- glTF is right-handed, C3 left-handed (`c3anim.AXIS`, docs/modding.md
#: 9.7), and a map that keeps the character's chirality between the two has to
#: be improper. See the module docstring, THE AXIS MAP.
AXIS_MAP = ((1.0, 0.0, 0.0),
            (0.0, 0.0, -1.0),
            (0.0, -1.0, 0.0))

#: The v1/v2 map: c3.X = -x, det +1. It played every rotation MIRRORED (the
#: owner's screenshots and the socket binding both say +x is the character's
#: LEFT; this put the donor's right hand there). Kept as the mirror CONTROL:
#: `retarget_clip(..., axis_map=MIRROR_AXIS_MAP)` with `mirrored_mapping()`
#: is the v1/v2 composition, and the `anatomy.side` check must FAIL it.
MIRROR_AXIS_MAP = ((-1.0, 0.0, 0.0),
                   (0.0, 0.0, -1.0),
                   (0.0, -1.0, 0.0))

#: Frame interval, ms. INFERRED (docs/animation.md section 5): every
#: action-locked 3DEffect row says 41; the engine's UV clock says 33.
DEFAULT_FPS_MS = 41.0

#: Mixamo joint (base name, prefix and `_nn` suffix stripped) -> c3 bone
#: in the p84 space. The +x chain (8-14, 42-45) is the character's LEFT --
#: decided by the game, not by a label (module docstring, THE AXIS MAP):
#: socket ordinal 1 is `v_l_weapon` and rides bone 11. `core/bonelabel.SIDE`
#: still says `.R` for +x (backlog item 43, PR #204); do not read sides off
#: its suffixes. Thumbs/fingers by geometry, see the docstring.
MAPPING = {
    "Hips": 1, "Spine": 2, "Spine1": 3, "Spine2": 4, "Neck": 5, "Head": 6,
    "LeftShoulder": 8, "LeftArm": 9, "LeftForeArm": 10, "LeftHand": 11,
    "LeftHandThumb1": 12, "LeftHandIndex1": 14,
    "RightShoulder": 16, "RightArm": 17, "RightForeArm": 18, "RightHand": 19,
    "RightHandThumb1": 20, "RightHandIndex1": 22,
    "LeftUpLeg": 42, "LeftLeg": 43, "LeftFoot": 44,
    "RightUpLeg": 47, "RightLeg": 48, "RightFoot": 49,
}

#: The PHY names of the two weapon sockets. The MOTI ordinal of each is
#: read off the TARGET's PHY order by `socket_ordinal`, because the
#: PHY<->MOTI binding is positional (docs/modding.md 11.1, MeshCreate
#: 0x28360 / MotionCreate 0x28470, 792/792 containers) and the order is a
#: property of the SHAPE: shapes 1/3/4 write v_armet, v_l_weapon,
#: v_r_weapon, v_body (270/271, 261/269, 263/263 PHY-carrying clips on the
#: CCO snapshot), shape 2 writes v_body FIRST (234/238), so `v_l_weapon` is
#: ordinal 1 there and 2 here. Until 2026-10-01 this was the constant
#: `LEFT_WEAPON_SOCKET = "1"`, and on shape 2 that ordinal is `v_armet`
#: (host 7, no centroid): three false FAILs on a geometrically correct
#: build. The body bone that hosts `v_l_weapon` is the character's LEFT
#: hand, and `anatomy.side` is keyed on THAT.
LEFT_WEAPON_PHY = "v_l_weapon"
RIGHT_WEAPON_PHY = "v_r_weapon"

#: The pelvis: the bone the root pivot turns about, whose height sets the
#: root-motion scale, and whose matrix the MOTION check measures the hand
#: against. `Target` REROOTS the rig here when `bonejoint.tree_from_joints`
#: rooted it elsewhere -- it roots at the most-jointed bone, which on
#: shape 4 is the CHEST (bone 4: joints with 3, 5, 8, 16, 30, 36 = 6,
#: against the pelvis's 5), so bone 1 had a parent and `rest_direction(1)`
#: subtracted the 1-2 joint from itself ("cannot normalise a zero vector",
#: 2026-10-01; the same on Guard 900, monster 126 and the Pharmacist NPC).
#: Shapes 2 and 3 worked only because their pelvis happens to carry the most
#: joints. `--map auto` passes the bone LABELLED pelvis instead
#: (`RigTables.pelvis`); this constant is the player shapes'.
PELVIS = 1

#: The finger bones. Rigid with their hands unless `--fingers`: MEASURED rest
#: offsets of 64.7 / 66.5 deg on the two thumbs (12 / 20; garment centroid
#: for 12, the garment does not skin 20) against 2-14 deg on every other bone
#: say their joints (solved from the few clips where fingers articulate) do
#: not locate a direction, and the `v_r_weapon` socket rides 20.
FINGERS = {12, 14, 20, 22}

#: Donor joint -> the child joint whose world position gives the bone's
#: direction. Every Mixamo joint has an end node, so no donor bone is a leaf;
#: the head is in `DONOR_FRAME_AXIS` instead (its end node names no segment
#: the c3 mesh has).
DONOR_DIRECTION_CHILD = {
    "Hips": "Spine", "Spine": "Spine1", "Spine1": "Spine2", "Spine2": "Neck",
    "Neck": "Head",
    "RightShoulder": "RightArm", "RightArm": "RightForeArm",
    "RightForeArm": "RightHand", "RightHand": "RightHandIndex1",
    "RightHandThumb1": "RightHandThumb2", "RightHandIndex1": "RightHandIndex2",
    "LeftShoulder": "LeftArm", "LeftArm": "LeftForeArm",
    "LeftForeArm": "LeftHand", "LeftHand": "LeftHandIndex1",
    "LeftHandThumb1": "LeftHandThumb2", "LeftHandIndex1": "LeftHandIndex2",
    "RightUpLeg": "RightLeg", "RightLeg": "RightFoot", "RightFoot": "RightToeBase",
    "LeftUpLeg": "LeftLeg", "LeftLeg": "LeftFoot", "LeftFoot": "LeftToeBase",
}

#: A direction that is a FRAME AXIS rather than a segment, on each side. The
#: head: the donor's `Head -> HeadTop_End` leans 6.35 deg forward of its own
#: up in rest, and the c3 skull top reads 0.8-9.1 deg forward of the 5-6 joint
#: depending on which "highest" vertices are averaged (six cuts, section 3 of
#: the doc) -- neither side has a measurable segment, both rigs stand upright
#: in rest, so both directions are the character's UP (+y in glTF, -Z in C3:
#: +Z is down) and the head's rest offset is 0.00 by construction. The head
#: then plays the donor's world delta bare. Recorded as `frameAxis` in the
#: manifest's `directionCentroidSource`. (The head's centroid, 11.9 deg
#: forward, would have pitched it 5.56 deg back in every frame -- the feet's
#: proxy trap again.)
DONOR_FRAME_AXIS = {"Head": (0.0, 1.0, 0.0)}
C3_FRAME_AXIS = {6: (0.0, 0.0, -1.0)}

#: c3 bone -> the child whose joint gives the bone's direction; None = leaf
#: (head joint -> own centroid, unless `C3_FRAME_AXIS` names the bone). The
#: hand points at its finger bone (the donor's Hand -> HandIndex1 likewise;
#: measured on both hands, the index knuckle lies within 1.2 deg of yaw of
#: the donor's forearm line, so that proxy costs ~1 deg). The feet point at
#: their TOES (44 -> 45, 49 -> 50), the donor's `Foot -> ToeBase`: the rig
#: has no 44-45 joint, so the toe's centroid stands in for it
#: (`Target.rest_direction`).
C3_DIRECTION_CHILD = {
    1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: None,
    8: 9, 9: 10, 10: 11, 11: 14, 12: None, 14: None,
    16: 17, 17: 18, 18: 19, 19: 22, 20: None, 22: None,
    42: 43, 43: 44, 44: 45, 47: 48, 48: 49, 49: 50,
}

#: The root's direction starts at the midpoint of THESE children's joints
#: (the hips), the mirror of the donor's `Spine - mid(UpLegs)`. Not "every
#: non-spine child": on p84 that set also holds the skirt flaps 52 and 64,
#: whose joints (y +8.0 / -11.36) pitched the pelvis 11 deg (module docstring).
C3_HIP_CHILDREN = {1: (42, 47)}

#: Bones skinned by real garments but absent from the reference mesh and so
#: from the rig: toe.R under foot.R, toe.L under foot.L
#: (docs/bone_labels_2026-09-21.md). Rigid with the named parent. The
#: default for a shape `DEFAULT_ATTACH_BY_SHAPE` does not name.
DEFAULT_ATTACH = {45: 44, 50: 49}

#: Per shape (`default_attach`). An attach makes a bone the SAME MATRIX as
#: its parent (`solve_translations`, "rigid"), so a default belongs here only
#: for a bone the vanilla clips carry as exactly that -- and the check that
#: says so is `socket_host_outside_rig`; this table is its answer, written
#: down so a run reads it in 0 s and a reader sees it without the clips.
#:
#: Shape 2 (2026-10-02): its `v_armet` socket -- the hair/hat mount, MOTI
#: ordinal 1 in its `v_body, v_armet, v_l_weapon, v_r_weapon` order -- rides
#: bone 7, which the shape-2 reference body skins nothing on, so the rig has
#: no place for 7 and the retarget CARRIED it from the vanilla clip. The
#: regenerated socket then followed vanilla's bone 7 and the hat sat 67-108
#: units from the dancing head in every frame (socket-vs-head rigidity
#: spread 41.3 / 49.4 units on dance1 / dance4 against 7e-05 in vanilla).
#: In vanilla bone 7 IS the head's matrix: max |M_7 - M_6| 1.5e-05 over
#: 2,375 keys of the first four family clips, the runner-up bone 153 units
#: off, and the constant M_7 . M_6^-1 is the identity to 1.6e-05 -- so
#: `7: 6` reproduces it exactly (the fixed builds differ from the broken
#: ones in bone 7 and the socket track alone; `docs/c3retarget_2026-09-30.md`
#: section 10). Shapes 3 and 4 add nothing: every socket host is in their
#: rigs (6/11/20 and 6/12/19), and the check derives nothing there.
DEFAULT_ATTACH_BY_SHAPE = {
    "2": {45: 44, 50: 49, 7: 6},
    "3": dict(DEFAULT_ATTACH),
    "4": dict(DEFAULT_ATTACH),
}

#: The garment whose `v_body` supplies direction centroids the reference
#: mesh lacks (the toes), PER SHAPE: `ini/3dobj.ini` maps the appearance
#: `00N188490` to `c3/mesh/00N188495.c3` for N = 1..4 and all four exist on
#: the CCO snapshot (measured 2026-10-01; 002/003/004 skin toes 45 and 50,
#: 001 skins 21 bones and no toes -- shape 1's reference body skins its
#: own toes 42/47). Until 2026-10-01 shape 3's mesh was the default for
#: EVERY shape: on shape 4 it supplied bone 12's centroid from the wrong
#: body (+76.5 against the shape-4 garment's +96.8) and read motion 0.536
#: (FAIL) against 0.905 with its own. `default_garment` resolves it; an
#: empty string means none (the clip's own reference body throughout).
DEFAULT_GARMENT_FMT = "c3/mesh/00%s188495.c3"
#: Shape 3's, kept for the callers and tests that name the owner's garment.
DEFAULT_GARMENT = DEFAULT_GARMENT_FMT % "3"

#: The bones whose posed vertices set the `hover` number: feet and toes.
FOOT_BONES = (44, 45, 49, 50)

#: The MOTION check's cut on the x-sign agreement. 0.75, from 0.9 on
#: 2026-10-01. The 0.9 was calibrated on shape 3 alone (0.972) and the
#: metric measures the hand's x against the PELVIS pivot, so it depends on
#: shoulder half-width against arm length (shape 1: shoulders at x +-11.9,
#: hand at 57; shape 4: +-29 and 92). MEASURED on the CCO snapshot with the
#: Gangnam donor, every composition the controls run (reader_targets,
#: controls1.py; shape 1 is the scratch p81 prototype's number):
#:
#:     shape / clip        shipped   swap alone   mirror alone   v1/v2
#:     1 / 003.c3          0.903     0.392        0.979          0.408
#:     1 / 004.c3          0.880     --           --             --
#:     2 / 001.c3          0.971     --           --             --
#:     2 / 003.c3          0.977     0.355        0.955          0.394
#:     2 / 004.c3          0.975     --           --             --
#:     3 / 003.c3          0.972     0.431        0.956          0.515
#:     4 / 003.c3          0.905     0.359        0.967          0.391
#:     4 / 004.c3          0.888     --           --             --
#:
#: The lowest CORRECT composition reads 0.880 (shape 4's 004.c3 reads 0.888:
#: a second correct build the 0.9 cut would have failed, found by the first
#: export under this cut) and the highest WRONG one that MOTION is meant to
#: catch (swap alone, v1/v2) reads 0.515 on shape 3 and 0.408 elsewhere;
#: 0.75 sits 0.13 under the lowest correct and 0.235 over the highest
#: wrong. (Mirror alone reads > 0.9 everywhere -- MOTION is
#: blind to it by construction, SIDE and det catch it; the docstring's
#: table.) `tests/test_c3retarget.py` asserts the cut sits inside that gap.
MOTION_CUT = 0.75

TAILS = ("loop", "hold")
#: `--ground`: the vertical root term that keeps the feet at the donor's
#: clearance (`retarget_clip`). Off (None) by default, so every player-route
#: build keeps its bytes. The gap is ``hover_c3 - hover_donor`` per frame
#: (the lowest posed foot point against the donor's lowest joint):
#:
#: * ``mean``   adds the clip's mean gap: ONE number, no new motion.
#: * ``frame``  adds each frame's own gap: the feet sit exactly at the
#:              donor's clearance on every frame.
#: * ``smooth`` adds `ground_smooth` of the gaps: never less lift than the
#:              frame needs, spread over +-`GROUND_SMOOTH_HALF` frames.
#:
#: MEASURED on the CCO snapshot with the Gangnam donor at 302 frames
#: (hover c3 min / mean / max; largest frame-to-frame step of the root's
#: vertical delta), 2026-10-02:
#:
#:                 monster 129 (bind knees bent 55 deg)   monster 126
#:     off         -23.44 / -14.89 /  -6.05   step  3.93   -8.21 / -2.76 /  1.62   4.66
#:     mean         -5.89 /   2.66 /  11.50   step  3.93   -2.29 /  3.15 /  7.53   4.66
#:     frame         2.05 /   2.66 /   4.56   step 13.42    2.43 /  3.15 /  5.41   8.39
#:     smooth        2.16 /   6.56 /  16.85   step  4.36    2.43 /  5.14 /  9.33   5.51
#:     (donor        2.05 /   2.66 /   4.56                 2.43 /  3.15 /  5.41)
#:
#: Straightening a crouched bind lengthens each leg by a different amount on
#: every frame (129's gap runs -26.3 .. -9.3 and jumps 12.5 in ONE frame
#: when the support leg changes), so `mean` still leaves the worst frame 5.9
#: under the sole and `frame` buys an exact clearance with a hip pop of up
#: to 13.4 units a frame. `smooth` is the builder's choice: feet never
#: below the donor's clearance, steps at the ungrounded size, at the price
#: of floating (129: 6.6 on average where the donor reads 2.7). On a body
#: with a straight bind the three differ by little (Guard 900: mean gap
#: -0.25; smooth lifts it 1.0 on average).
GROUNDS = ("mean", "frame", "smooth")
#: `--ground smooth`: the half-width, in frames, of the window its lower
#: envelope and its average run over (`ground_smooth`). Measured on 129:
#: half 1 -> step 7.21, 2 -> 4.36, 3 -> 4.24, 5 -> 4.21 (mean hover 5.06 /
#: 6.56 / 7.27 / 8.03): 2 is where the step stops falling.
GROUND_SMOOTH_HALF = 2


def ground_smooth(gaps: list, half: int = None) -> list:
    """The per-frame hover gaps as a SMOOTH offset that never under-lifts.

    ``low[i]`` is the smallest (most sunk) gap within `half` frames of i,
    and the result is the average of ``low`` over the same window. Every
    ``low[i]`` in the window around k was taken over a span that contains
    k, so each is <= gaps[k] and so is their average: the offset lifts the
    body at least as much as frame k needs, on every frame -- the feet
    never sit below the donor's clearance -- while a one-frame jump in the
    gap (the support leg changing: 12.5 units on monster 129) is spread
    over the window instead of popping the hips."""
    n = len(gaps)
    half = max(0, int(GROUND_SMOOTH_HALF if half is None else half))

    def span(k):
        return range(max(0, k - half), min(n, k + half + 1))
    low = [min(gaps[i] for i in span(k)) for k in range(n)]
    return [sum(low[i] for i in span(k)) / len(span(k)) for k in range(n)]


#: `--hop-cap` (the BUILDER's default; this tool's own default is None, so
#: every existing `--ground` build keeps its bytes): the most a frame's
#: hover may EXCEED the donor's clearance under `smooth`, in c3 units.
#:
#: The lower envelope lifts the body for the frames whose legs are
#: straightest; on a frame where both knees bend more than the donor's the
#: feet come up with them, and a crouched bind amplifies that (monster
#: 129: 54.7 deg of knee in bind, 9.9 units of leg when straightened). The
#: excess ``gaps[k] - off[k]`` is that hop. MEASURED 2026-10-03 on the
#: 2026-10-03 set, max excess per family: Guard 900 2.68, Pharmacist 4.05,
#: Boxer 4.32, 126 5.93 | Maud/Pedlar 9.34, 9992670 9.75, 153 11.11,
#: 129 13.33 (hover max 16.85 against the donor's 4.56). WIDENING the window
#: makes it worse, not better (129 at half 3/4/5: 14.2/14.3/14.4), because
#: a wider envelope lifts more. The cap trades the hop for a hip step of
#: the same size (`ground_cap`: it cannot under-lift, so it can only follow
#: the gap up AT the spike); 8.0 sits in the measured gap -- the first four
#: families are byte-identical (0 frames clamped); 129, rebuilt, reads
#: hover max 12.0 / mean 6.26 with the offset's own step 5.89 (was 16.85 /
#: 6.56 / 2.42; `maxStepPerFrame`) and the root's total z step, the
#: donor's own vertical motion on top, 7.44 (smooth 4.4, ungrounded 3.93);
#: 153 clamps 3 frames (hover max 16.6 -> 14.2). Cap 6 would have read
#: 9.5 / 7.9 on 129 and left 126 0.07 units from clamping. The cap trims
#: the peaks only: on donor-flat frames 129's feet still sit 10-16 up
#: (section 13.3 of the doc has why -- the crouched bind's reach on the
#: donor's toe-down frames drives the envelope, a different ground model's
#: problem, not a cap's).
GROUND_HOP_CAP = 8.0


def ground_cap(gaps: list, off: list, cap) -> list:
    """`off` (a grounding offset with ``off[k] <= gaps[k]``) with every
    frame's hover excess over the donor's clearance, ``gaps[k] - off[k]``,
    clamped to `cap`: ``max(off[k], gaps[k] - cap)``.

    Never under-lifts (``gaps - cap <= gaps``), never lowers the offset,
    and is the identity wherever the excess already fits -- so a family
    under the cap keeps its bytes. ``cap`` 0 is `frame` mode; None is no
    cap. The price, by construction, is a root step of (excess - cap) on
    the frame the excess appears (`GROUND_HOP_CAP` has the numbers)."""
    if cap is None:
        return list(off)
    cap = float(cap)
    if cap < 0.0:
        raise RetargetError("--hop-cap must be >= 0 (0 = the frame's own gap), got %r" % (cap,))
    if len(off) != len(gaps):
        raise RetargetError("ground_cap: %d offsets for %d gaps" % (len(off), len(gaps)))
    return [max(float(o), float(g) - cap) for o, g in zip(off, gaps)]
ROOT_MOTIONS = ("inplace", "vertical", "full")
REST_OFFSETS = ("none", "direction", "conjugate")


class RetargetError(ValueError):
    """A donor or target this tool cannot honestly retarget."""


# ---------------------------------------------------------------------------
# 3-vectors and 3x3s, column-vector convention: R . v
# ---------------------------------------------------------------------------

def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_unit(a):
    n = v_len(a)
    if n < 1e-30:
        raise RetargetError("cannot normalise a zero vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def m3_ident():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def m3_mul(a, b):
    return tuple(tuple(a[r][0] * b[0][c] + a[r][1] * b[1][c] + a[r][2] * b[2][c]
                       for c in range(3)) for r in range(3))


def m3_T(a):
    return ((a[0][0], a[1][0], a[2][0]),
            (a[0][1], a[1][1], a[2][1]),
            (a[0][2], a[1][2], a[2][2]))


def m3_apply(a, v):
    """``a . v`` for a column vector."""
    return (a[0][0] * v[0] + a[0][1] * v[1] + a[0][2] * v[2],
            a[1][0] * v[0] + a[1][1] * v[1] + a[1][2] * v[2],
            a[2][0] * v[0] + a[2][1] * v[1] + a[2][2] * v[2])


def m3_det(a):
    return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
            - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))


def m3_of_m4(m4):
    """The upper-left 3x3 of a column-vector 4x4 (rows)."""
    return ((m4[0][0], m4[0][1], m4[0][2]),
            (m4[1][0], m4[1][1], m4[1][2]),
            (m4[2][0], m4[2][1], m4[2][2]))


def orthonormalise(m):
    """The nearest-enough proper rotation to a 3x3 that may carry scale.

    Gram-Schmidt over the COLUMNS (in column-vector form the columns are
    the rotated basis vectors), third column from the cross product so the
    result is right-handed whatever the input's determinant. A skeleton
    world matrix here carries the root chain's uniform 1.8906 scale and
    nothing else; this strips it. A negative-determinant input (a mirrored
    node) is reported by `m3_det` on the input, not silently fixed here --
    the caller decides.
    """
    c0 = (m[0][0], m[1][0], m[2][0])
    c1 = (m[0][1], m[1][1], m[2][1])
    c0 = v_unit(c0)
    c1 = v_sub(c1, v_scale(c0, v_dot(c1, c0)))
    c1 = v_unit(c1)
    c2 = v_cross(c0, c1)
    return ((c0[0], c1[0], c2[0]),
            (c0[1], c1[1], c2[1]),
            (c0[2], c1[2], c2[2]))


def rotation_between(a, b):
    """The shortest rotation ``C`` with ``C . unit(a) = unit(b)``.

    Rodrigues: ``I + [v]x + [v]x^2 / (1 + c)`` with ``v = a x b``,
    ``c = a . b``. The anti-parallel case (``c -> -1``) has no shortest
    arc; any 180-degree turn about an axis perpendicular to `a` works and
    the least-aligned basis vector picks one deterministically.
    """
    a = v_unit(a)
    b = v_unit(b)
    v = v_cross(a, b)
    c = v_dot(a, b)
    if c < -1.0 + 1e-12:
        # 180 degrees about any axis perpendicular to a.
        k = min(range(3), key=lambda i: abs(a[i]))
        e = tuple(1.0 if i == k else 0.0 for i in range(3))
        axis = v_unit(v_cross(a, e))
        x, y, z = axis
        return ((2 * x * x - 1, 2 * x * y, 2 * x * z),
                (2 * x * y, 2 * y * y - 1, 2 * y * z),
                (2 * x * z, 2 * y * z, 2 * z * z - 1))
    vx = ((0.0, -v[2], v[1]), (v[2], 0.0, -v[0]), (-v[1], v[0], 0.0))
    vx2 = m3_mul(vx, vx)
    f = 1.0 / (1.0 + c)
    return tuple(tuple((1.0 if r == cc else 0.0) + vx[r][cc] + vx2[r][cc] * f
                       for cc in range(3)) for r in range(3))


def rotation_angle_deg(R):
    """The angle of a rotation matrix, degrees."""
    tr = R[0][0] + R[1][1] + R[2][2]
    return math.degrees(math.acos(max(-1.0, min(1.0, (tr - 1.0) * 0.5))))


def angle_between_deg(a, b):
    a = v_unit(a)
    b = v_unit(b)
    return math.degrees(math.acos(max(-1.0, min(1.0, v_dot(a, b)))))


def axis_convert(v, A=None):
    """A glTF-world vector in c3 axes (`A` overrides the shipped map: the
    mirror control passes `MIRROR_AXIS_MAP`)."""
    return m3_apply(AXIS_MAP if A is None else A, v)


def conjugate_by_axis(D, A=None):
    """A glTF-world rotation re-expressed in c3 axes: ``A . D . A^T``.

    With det(A) = -1 this is STILL a proper rotation (det D' = det(A)^2 .
    det(D) = +1): the same turn seen from inside a left-handed frame, which
    is exactly what keeps the character's chirality across the handedness
    change. ``A . (D . v) = D' . (A . v)`` for every v, whatever det(A) is.
    """
    A = AXIS_MAP if A is None else A
    return m3_mul(A, m3_mul(D, m3_T(A)))


def axis_note(A) -> str:
    """``c3.X = +x_gltf, ...; det -1 (...)`` for a signed-permutation map."""
    names = "xyz"
    parts = []
    for r, row in enumerate(A):
        j = max(range(3), key=lambda c: abs(row[c]))
        parts.append("c3.%s = %s%s_gltf" % ("XYZ"[r], "+" if row[j] > 0 else "-",
                                            names[j]))
    det = m3_det(A)
    why = ("glTF right-handed -> C3 left-handed" if det < 0
           else "handedness KEPT: a mirror image of the character")
    return "%s; det %+d (%s)" % (", ".join(parts), round(det), why)


def mirrored_mapping(mapping: dict = None) -> dict:
    """The table with every Left*/Right* name exchanged -- the mapping half of
    the v1/v2 composition, kept for the mirror CONTROL."""
    out = {}
    for n, b in (MAPPING if mapping is None else mapping).items():
        if n.startswith("Left"):
            out["Right" + n[4:]] = b
        elif n.startswith("Right"):
            out["Left" + n[5:]] = b
        else:
            out[n] = b
    return out


def socket_ordinal(data: bytes, name: str = LEFT_WEAPON_PHY, rig=None) -> tuple:
    """``(ordinal, how)``: the MOTI ordinal of the socket whose PHY is `name`
    in the target container `data`, as a string key into
    `rig.socket_constants`; ``(None, why)`` when it cannot be read.

    The PHY<->MOTI binding is positional (docs/modding.md 11.1), so the
    index of the PHY named `name` IS the ordinal -- when the target carries
    that name EXACTLY ONCE. Otherwise (17-24 clips per shape on CCO carry
    no PHY at all; 1-4 per shape name the left socket `v_r_weapon01`; 4
    shape-3 clips name two `v_r_weapon`) the FAMILY's modal PHY order,
    recorded on the rig by `bonerig.solve_family` as
    `solved_from.phyOrder`, decides, if it names `name` once. A rig saved
    before that field existed has no fallback and the caller reports the
    side check as un-keyable, as it did before for a missing socket.

    Never the constant `"1"`: that is shape 3's answer (and 1's and 4's),
    and on shape 2 it is `v_armet`.
    """
    name = str(name).lower()
    names = bonerig.phy_names(data)
    if names.count(name) == 1:
        return str(names.index(name)), (
            "PHY %r is chunk %d of the target's %s; binding positional "
            "(docs/modding.md 11.1)" % (name, names.index(name), list(names)))
    fam = tuple(str(n).lower() for n in
                ((getattr(rig, "solved_from", None) or {}).get("phyOrder") or ()))
    if fam.count(name) == 1:
        sf = rig.solved_from
        return str(fam.index(name)), (
            "target's PHY names %s name %r %d times, not once; the family's "
            "modal PHY order %s does (chunk %d; %s of %s PHY-carrying clips agree)"
            % (list(names), name, names.count(name), list(fam), fam.index(name),
               sf.get("phyOrderClips"), sf.get("phyClips")))
    if names:
        why = "target's PHY names %s name %r %d times" % (list(names), name,
                                                           names.count(name))
    else:
        why = "target carries no PHY chunk"
    if fam:
        why += "; the family's modal PHY order %s names it %d times" % (list(fam),
                                                                       fam.count(name))
    else:
        why += "; the rig records no family PHY order (solved before 2026-10-01?)"
    return None, why


def left_hand_bone(rig, ordinal) -> tuple:
    """``(bone, how)``: the body bone that hosts socket `ordinal`, the
    `v_l_weapon` track by `socket_ordinal` -- the character's LEFT hand (or
    a finger of it: shape 4's rides thumb 12) by the game's own binding.
    ``(None, why)`` when `ordinal` is None or the rig has no solved constant
    for that socket."""
    if ordinal is None:
        return None, "no v_l_weapon socket ordinal for this target"
    meta = (getattr(rig, "socket_constants", None) or {}).get(str(ordinal)) or {}
    host = meta.get("host")
    if host is None:
        return None, ("rig has no solved socket %s (v_l_weapon): %s"
                      % (ordinal, meta.get("why") or "not in socketConstants"))
    return int(host), ("socket %s (v_l_weapon) rides bone %d: PHY<->MOTI binding "
                       "positional (docs/modding.md 11.1), the ordinal read off the "
                       "target's PHY names" % (ordinal, int(host)))


def mapping_ok(host, hand, parents: dict, fingers=FINGERS) -> bool:
    """Does the donor's LeftHand drive the bone the game calls the left
    weapon hand? ``host`` is the `v_l_weapon` socket's host (`left_hand_bone`),
    ``hand`` the bone `MAPPING["LeftHand"]` names, ``parents`` the rig's.

    THE RULE: ``host == hand``, OR ``host`` is a FINGER (`FINGERS`) whose
    rig parent is ``hand``. The second arm is shape 4 (2026-10-01): its
    socket rides THUMB 12 (spread 2.2e-05), a child of hand 11 that is
    rigid with it unless `--fingers` -- and with `--fingers` it is driven
    by the donor's LeftHandThumb1, still the left side. The same asymmetry
    sits unchecked on the right: shape 3's `v_r_weapon` rides thumb 20,
    shape 2's finger 22, shape 4's hand 19.

    WHY A MIRRORED COMPOSITION STILL FAILS IT: `mirrored_mapping()` maps the
    donor's LeftHand to 19 (the -x hand). Host 12's parent is 11, not 19,
    and 12 != 19, so both arms are False -- the rule tests the host's OWN
    parent against the mapped hand, not "is the host a finger of some
    hand". Measured on shape 4: shipped True, swap-alone False (motion
    0.359), v1/v2 False; and True on shapes 1/2/3 as before, where the
    host IS the hand.
    """
    if host is None or hand is None:
        return False
    host, hand = int(host), int(hand)
    if host == hand:
        return True
    return host in fingers and parents.get(host) == hand


def default_garment(shape, assets=None) -> tuple:
    """``(path, how)``: the garment `--garment` defaults to for `shape`.

    `DEFAULT_GARMENT_FMT % shape` when `assets` (an `AssetRoot`) has it --
    all four do on the CCO snapshot -- else '' (none: the clip's own
    reference body supplies every centroid) with `how` saying so. The
    manifest records both, so a build whose feet pointed at the wrong
    body's toes can be told from one that had no toes at all.
    """
    cand = DEFAULT_GARMENT_FMT % str(shape).strip()
    if assets is None:
        return cand, "default for shape %s (existence not checked)" % shape
    try:
        present = bool(assets.exists(cand))
    except Exception:                                        # noqa: BLE001
        present = False
    if present:
        return cand, "default for shape %s: ini/3dobj.ini 00%s188490 -> %s" % (
            shape, str(shape).strip(), cand)
    return "", ("%s is not in this install; no garment -- the target's own "
                "reference body supplies every direction centroid" % cand)


def default_attach(shape) -> tuple:
    """``(attach, source)``: the `--attach` default for `shape` --
    `DEFAULT_ATTACH_BY_SHAPE[shape]` when the table names the shape, else
    `DEFAULT_ATTACH` (every shape's toes). `source` is the string the
    manifest records against each bone (`attachSource`): 'default for
    shape 2' against 'user' or 'derived'."""
    key = str(shape).strip() if shape is not None else ""
    if key in DEFAULT_ATTACH_BY_SHAPE:
        return dict(DEFAULT_ATTACH_BY_SHAPE[key]), "default for shape %s" % key
    return dict(DEFAULT_ATTACH), "default (no per-shape entry for shape %r)" % key


def socket_hosts_outside_rig(rig, parents=None, *, pelvis=PELVIS) -> dict:
    """``{host: [ordinals]}``: every solved socket whose host bone the rig
    gives NO PLACE -- no parent in `parents` (the rig's, or a `Target`'s
    before its attaches) and not the pelvis root. Such a host is CARRIED
    from the vanilla clip by `solve_translations`, and the socket then
    follows the vanilla bone instead of the dancing one (shape 2's hat).
    Empty on shapes 3 and 4 (hosts 6/11/20, 6/12/19 all placed). `pelvis`
    is the root the retarget drives: `PELVIS` on the player shapes, the
    LABELLED pelvis (`RigTables.pelvis`) on a monster/NPC target -- the
    check runs on every route."""
    parents = {int(k): v for k, v in
               (parents if parents is not None else rig.parents).items()}
    out: dict = {}
    for o, meta in sorted((rig.socket_constants or {}).items(), key=lambda kv: int(kv[0])):
        h = meta.get("host")
        if h is not None and int(h) != int(pelvis) and parents.get(int(h)) is None:
            out.setdefault(int(h), []).append(int(o))
    return out


def socket_host_outside_rig(rig, assets, *, parents=None, clips=4,
                            tolerance=bonerig.SOCKET_TOLERANCE,
                            pelvis=PELVIS) -> dict:
    """The attach each socket host outside the rig needs, DERIVED from the
    family's clips (`rig.solved_from.files`, the first `clips` that parse).

    -> ``{host: {"sockets", "attach", "spread", "runnerUp", "keys",
    "clips", "tolerance"}}``, one row per `socket_hosts_outside_rig` host.
    `attach` is the placed rig bone whose matrix the host EQUALS on every
    key read -- ``max |M_host - M_bone|`` over all 16 elements and all keys
    at most `tolerance` -- else None. Equality of the matrices, not a
    constant offset: an attach copies the parent's matrix, so a host rigid
    with a bone at some other offset cannot be expressed by one and the
    row says so (`attach` None, `spread` the best distance, `runnerUp` the
    second bone). Measured on the CCO snapshot: shape 2 derives ``{7: 6}``
    at 1.5e-05 over 2,375 keys (runner-up 45 at 153.6); shapes 3 and 4
    have no host outside the rig and derive nothing. 0.7 s for four clips.
    """
    hosts = socket_hosts_outside_rig(rig, parents, pelvis=pelvis)
    if not hosts:
        return {}
    par = {int(k): v for k, v in (parents if parents is not None else rig.parents).items()}
    placed = sorted({b for b, p in par.items() if p is not None} | {int(pelvis)})
    # host -> bone -> max |diff|; every candidate starts at 0.0 so an EXACT
    # match (the hermetic fixture) ranks, not only one with float noise
    dev = {h: {b: 0.0 for b in placed} for h in hosts}
    n_keys = n_clips = 0
    for f in ((getattr(rig, "solved_from", None) or {}).get("files") or [])[:clips]:
        try:
            chunks = list(c3phy.iter_chunks(assets.read(f["path"])))
            body = effects.parse_moti(chunks[c3rig._moti_slots(chunks)[rig.body_ordinal]][1])
        except Exception:                                    # noqa: BLE001
            continue                   # a clip this cannot read casts no vote
        if int(body.bone_count) != int(rig.bone_count):
            continue
        n_clips += 1
        for key in body.keys:
            n_keys += 1
            for h in hosts:
                mh = key.matrices[h]
                for b in placed:
                    d = max(abs(float(x) - float(y)) for x, y in zip(mh, key.matrices[b]))
                    if d > dev[h].get(b, 0.0):
                        dev[h][b] = d
    out: dict = {}
    for h, ordinals in hosts.items():
        # no key read = no evidence = no verdict (never "0.0 from every bone")
        ranked = sorted(dev[h].items(), key=lambda kv: kv[1]) if n_keys else []
        best = ranked[0] if ranked else None
        out[h] = {"sockets": ordinals,
                  "attach": best[0] if best and best[1] <= tolerance else None,
                  "nearest": best[0] if best else None,
                  "spread": round(best[1], 9) if best else None,
                  "runnerUp": [ranked[1][0], round(ranked[1][1], 4)] if len(ranked) > 1 else None,
                  "keys": n_keys, "clips": n_clips, "tolerance": tolerance}
    return out


# ---------------------------------------------------------------------------
# THE CONVENTION BOUNDARY: column-form rotations <-> MOTI row-vector floats
# ---------------------------------------------------------------------------

def col3_to_m16(R, t=(0.0, 0.0, 0.0)):
    """A column-form rotation and a translation as the 16 MOTI floats.

    Row-vector form is the transpose: float ``i*4 + j`` (row i, column j
    of the stored matrix) is ``R[j][i]``. Translation in row 3. PROVED
    against `effects.quat_to_matrix` in `tests/test_c3retarget.py`.
    """
    return (R[0][0], R[1][0], R[2][0], 0.0,
            R[0][1], R[1][1], R[2][1], 0.0,
            R[0][2], R[1][2], R[2][2], 0.0,
            float(t[0]), float(t[1]), float(t[2]), 1.0)


def m16_to_col3(m):
    """The inverse of `col3_to_m16`: ``(R column-form, t)``."""
    R = ((m[0], m[4], m[8]),
         (m[1], m[5], m[9]),
         (m[2], m[6], m[10]))
    return R, (m[12], m[13], m[14])


def xf_row(m, p):
    """``[x y z 1] . M`` -- how the engine poses a bind vertex. The same
    application `tests/test_bonerig.py` verifies the joint points with."""
    return (p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12],
            p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13],
            p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14])


def dir_row(m, d):
    """A DIRECTION under the stored 3x3 only: ``d . S``."""
    return (d[0] * m[0] + d[1] * m[4] + d[2] * m[8],
            d[0] * m[1] + d[1] * m[5] + d[2] * m[9],
            d[0] * m[2] + d[1] * m[6] + d[2] * m[10])


def joint_key(a, b) -> str:
    return "%d-%d" % (min(int(a), int(b)), max(int(a), int(b)))


def joint_residual(m_p, m_b, J) -> float:
    """How far apart the two matrices put the joint they should share."""
    return v_len(v_sub(xf_row(m_p, J), xf_row(m_b, J)))


def world_rotations(m16s: dict) -> dict:
    """Per-bone column-form rotations read back out of stored matrices --
    the 'decompose' half of the convention round trip."""
    return {b: m16_to_col3(m)[0] for b, m in m16s.items()}


def solve_translations(rotations: dict, parents: dict, joints: dict,
                       root_pivots: dict, bone_count: int,
                       warnings=None) -> tuple:
    """Absolute matrices from per-bone rotations, joints kept connected.

    -> ``(m16s, kinds)``: ``{bone: 16 floats}`` for every bone that got a
    matrix and ``{bone: "mapped" | "rigid"}`` saying how. Bones with no
    rig parent and no rotation get nothing (the caller CARRIES them).

    Root-first, so a parent's matrix exists when its child needs it.
    `rotations` are column-form; `joints` is the rig's ``"lo-hi"`` table
    in MOTI space; `root_pivots` is ``{root bone: (pivot point, delta)}``.
    """
    m16s: dict = {}
    kinds: dict = {}
    for b in c3anim.root_first(parents, bone_count):
        p = parents.get(b)
        R = rotations.get(b)
        if p is None:
            if R is None:
                continue                                      # carried
            P, delta = root_pivots.get(b, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
            S = m3_T(R)
            t = v_add(v_sub(P, m3_apply(m3_T(S), P)), delta)  # P - P.S
            m16s[b] = col3_to_m16(R, t)
            kinds[b] = "mapped"
            continue
        if int(p) not in m16s:
            if warnings is not None:
                warnings.append("bone %d hangs off %d which has no matrix; "
                                "carried" % (b, p))
            continue
        J = joints.get(joint_key(p, b))
        if R is None or J is None:
            if R is not None and J is None and warnings is not None:
                warnings.append("bone %d is mapped but the rig has no %s "
                                "joint; made rigid with %d"
                                % (b, joint_key(p, b), p))
            m16s[b] = m16s[int(p)]
            kinds[b] = "rigid"
            continue
        mp = m16s[int(p)]
        S = m3_T(R)
        jp = xf_row(mp, J)                               # J . S_p + t_p
        jb = m3_apply(m3_T(S), J)                        # J . S_b  (row form)
        t = v_sub(jp, jb)
        m16s[b] = col3_to_m16(R, t)
        kinds[b] = "mapped"
    return m16s, kinds


# ---------------------------------------------------------------------------
# the donor
# ---------------------------------------------------------------------------

_SUFFIX = re.compile(r"_\d+$")


def frame_direction(Rw_t, Rw_rest, axis) -> tuple:
    """A frame axis carried by a joint's world delta: ``Rw(t) . Rw(rest)^T .
    axis``. At rest it is `axis`; after a 30 deg nod it is the nodded axis."""
    return m3_apply(m3_mul(Rw_t, m3_T(Rw_rest)), axis)


def base_name(node_name: str) -> str:
    """``mixamorig:RightArm_00`` -> ``RightArm``. Prefix up to the last colon
    and the exporter's numeric suffix are both dropped."""
    n = str(node_name or "").split(":")[-1]
    return _SUFFIX.sub("", n)


class Donor:
    """The glTF clip with its joints resolved by Mixamo base name."""

    def __init__(self, gltf: gltfread.Gltf, animation: int = 0,
                 mapping: dict = None):
        self.g = gltf
        self.anim = gltf.animation(animation)
        self.mapping = dict(MAPPING if mapping is None else mapping)
        self.node_of: dict = {}
        dup = []
        for i in range(gltf.node_count):
            nm = gltf.node_name(i)
            if not nm:
                continue
            b = base_name(nm)
            if b in self.node_of:
                dup.append(b)
                continue
            self.node_of[b] = i
        self.warnings: list = []
        # Sketchfab's `Object_2` / `Object_4` / ... all strip to `Object`;
        # only a collision on a name this tool READS is worth a warning.
        needed = set(self.mapping) | set(DONOR_DIRECTION_CHILD.values()) | {"RightUpLeg", "LeftUpLeg", "Spine"}
        dup_needed = sorted(set(dup) & needed)
        if dup_needed:
            self.warnings.append("duplicate donor base names among the joints "
                                 "used, first kept: %s" % dup_needed)
        missing = [n for n in self.mapping if n not in self.node_of]
        if missing:
            raise RetargetError("donor has no node for mapped joint(s) %s; "
                                "nodes seen: %s" % (missing,
                                                    sorted(self.node_of)[:12]))
        for n, child in DONOR_DIRECTION_CHILD.items():
            if n in self.mapping and child not in self.node_of:
                raise RetargetError("donor has %s but not its direction child "
                                    "%s" % (n, child))
        for extra in ("RightUpLeg", "LeftUpLeg", "Spine"):
            if extra not in self.node_of:
                raise RetargetError("donor has no %s node" % extra)
        for ch in self.anim.channels:
            if ch.interpolation == "CUBICSPLINE":
                raise RetargetError(
                    "donor channel %s on node %d (%s) is CUBICSPLINE; this "
                    "tool samples LINEAR and STEP only"
                    % (ch.path, ch.node, gltf.node_name(ch.node)))
        self.rest_world = gltf.world_matrices()
        self.rest_rot = {n: self._rot(self.rest_world[i])
                         for n, i in self.node_of.items()}
        self.rest_pos = {n: gltf.position(self.rest_world[i])
                         for n, i in self.node_of.items()}
        dets = {n: m3_det(m3_of_m4(self.rest_world[i]))
                for n, i in self.node_of.items() if n in self.mapping}
        neg = [n for n, d in dets.items() if d < 0]
        if neg:
            raise RetargetError("mirrored (negative-determinant) donor joints: "
                                "%s" % neg)
        self.duration = float(self.anim.duration)
        # The joints `hover` measures the donor's lowest point over: the
        # skeleton under the mapped joints (the 41 `mixamorig` nodes here; the
        # skin lists a 42nd, Sketchfab's static `_rootJoint`, at the origin).
        # `node_of` also holds the scene's static nodes -- Sketchfab_model,
        # the .fbx node, Object_*, RootNode, _rootJoint, Human -- all at world
        # y = 0 in every frame, and a minimum over THOSE reads the origin's
        # height above the rest floor whenever no toe dips below it: the v3
        # manifest's donor hover max 3.268 was exactly (0 - (-0.0552)) * 59.157.
        self.hover_joints = self._skeleton_names()

    def _skeleton_names(self) -> list:
        """Base names of every node under a mapped joint (the mapped joints
        included): the articulated skeleton, and nothing static."""
        idx: set = set()

        def walk(i):
            if i in idx:
                return
            idx.add(i)
            for c in self.g.children(i):
                walk(c)
        for n in self.mapping:
            walk(self.node_of[n])
        return sorted(n for n, i in self.node_of.items() if i in idx)

    @staticmethod
    def _rot(m4):
        return orthonormalise(m3_of_m4(m4))

    def pose(self, t: float) -> list:
        """World matrices at time `t` (clamped to the clip by the sampler)."""
        return self.g.world_matrices(self.g.sample(self.anim, t))

    def rotation(self, name: str, world) -> tuple:
        return self._rot(world[self.node_of[name]])

    def position(self, name: str, world) -> tuple:
        return self.g.position(world[self.node_of[name]])

    def direction(self, name: str, world) -> tuple:
        """The bone's world direction (glTF axes, unnormalised). Hips: the
        spine joint minus the hip midpoint. A joint in `DONOR_FRAME_AXIS`
        (the head): its rest frame axis carried by its world delta,
        ``Rw(t) . Rw(rest)^T . axis`` -- the axis itself at rest."""
        if name == "Hips":
            mid = v_scale(v_add(self.position("RightUpLeg", world),
                                self.position("LeftUpLeg", world)), 0.5)
            return v_sub(self.position("Spine", world), mid)
        axis = DONOR_FRAME_AXIS.get(name)
        if axis is not None:
            return frame_direction(self.rotation(name, world),
                                   self.rest_rot[name], axis)
        child = DONOR_DIRECTION_CHILD[name]
        return v_sub(self.position(child, world), self.position(name, world))

    def rest_direction(self, name: str) -> tuple:
        return self.direction(name, self.rest_world)

    #: The donor's UP in glTF axes, the frame axis `DONOR_FRAME_AXIS` gives
    #: the head; a c3 bone that takes the frame-axis rule (`RigTables.
    #: frame_axis`: the head, or a headless neck) is measured against THIS
    #: on the donor side too, so the offset is 0.00 by construction.
    UP = (0.0, 1.0, 0.0)

    def axis_direction(self, name: str, world, axis=UP) -> tuple:
        """`axis` carried by `name`'s world delta -- `direction` for a joint
        in `DONOR_FRAME_AXIS`, available for ANY joint when the c3 side says
        frame axis."""
        return frame_direction(self.rotation(name, world), self.rest_rot[name], axis)

    def hips_height(self) -> float:
        """Hips above the lowest rest joint (the toe ends), world units."""
        floor = min(p[1] for p in self.rest_pos.values())
        return self.rest_pos["Hips"][1] - floor

    def describe(self) -> dict:
        return {
            "source": self.g.source,
            "generator": self.g.doc["asset"].get("generator"),
            "nodes": self.g.node_count,
            "skins": self.g.skin_count(),
            "animation": {"index": self.anim.index, "name": self.anim.name,
                          "channels": len(self.anim.channels),
                          "animatedNodes": len(self.anim.animated_nodes()),
                          "keys": sum(len(c.times) for c in self.anim.channels),
                          "durationS": self.duration,
                          "interpolation": sorted({c.interpolation for c in
                                                   self.anim.channels})},
            "restHipsWorld": list(self.rest_pos["Hips"]),
            "restHeadWorld": list(self.rest_pos.get("Head", (0, 0, 0))),
            "hipsHeightAboveFeet": self.hips_height(),
        }


# ---------------------------------------------------------------------------
# the target
# ---------------------------------------------------------------------------

def reference_mesh_from_clip(data: bytes):
    """The skinned `v_body` PHY in a clip, chunk matrix APPLIED, or None."""
    for tag, body in c3phy.iter_chunks(data):
        if tag[:3] != b"PHY":
            continue
        try:
            m = c3phy.parse_phy(tag, body)
        except Exception:                                    # noqa: BLE001
            continue
        if not (m.name or "").lower().startswith("v_body"):
            continue
        if max((int(v.bone0) for v in m.vertices), default=0) + 1 < 2:
            continue
        return c3phy.apply_matrix_copy(m)
    return None


def garment_mesh(data: bytes):
    """The skinned `v_body` PHY of a garment file, chunk matrix APPLIED, or
    None. The same shape `reference_mesh_from_clip` returns, read from a
    `c3/mesh/*.c3` instead of a clip."""
    return reference_mesh_from_clip(data)


class Target:
    """The c3 side: the rig's joints and the bind geometry, MOTI space.

    `mesh` is the reference body (the clip's own `v_body`): its centroids
    set the pelvis pivot and its sole the floor. `garment` is the body that
    will WEAR the clip; where it skins a bone its centroid is the direction
    proxy (the toes exist only there), else the reference mesh's is.
    """

    def __init__(self, rig, mesh, attach: dict = None, garment=None,
                 pelvis=PELVIS):
        self.rig = rig
        self.parents = {int(k): (None if v is None else int(v))
                        for k, v in rig.parents.items()}
        #: ``{"bone": pelvis, "wasRootedAt": old root}`` when the rig came
        #: rooted elsewhere and was re-hung from the pelvis here; None when
        #: it already was (shapes 2/3: a no-op, same bytes measured).
        self.rerooted = None
        if pelvis is not None and int(pelvis) in self.parents \
                and self.parents.get(int(pelvis)) is not None:
            import bonelabel                                 # noqa: PLC0415
            old = int(pelvis)
            guard = 0
            while self.parents.get(old) is not None and guard <= len(self.parents):
                old = self.parents[old]
                guard += 1
            self.parents = {int(k): (None if v is None else int(v)) for k, v in
                            bonelabel._reroot_at(self.parents, int(pelvis)).items()}
            self.rerooted = {"bone": int(pelvis), "wasRootedAt": old}
        #: the rig's own parents (after the reroot, before any attach):
        #: what `socket_hosts_outside_rig` judges "outside the rig" against
        self.rig_parents = dict(self.parents)
        self.attach = {}
        #: ``{bone: {"asked": p, "rigParent": q}}`` -- every attach NOT
        #: applied because the rig gives the bone a parent of its own
        self.attach_refused: dict = {}
        #: fallbacks taken while resolving directions, and every attach
        #: refused; the caller reports them
        self.notes: list = []
        for b, p in (attach or {}).items():
            self.add_attach(b, p)
        self.joints = {k: tuple(float(x) for x in v)
                       for k, v in (rig.joint_points or {}).items()}
        self.mesh = mesh
        self.garment = garment
        self.centroids = bonetree.bone_centroids(mesh.vertices) if mesh else {}
        self.garment_centroids = (bonetree.bone_centroids(garment.vertices)
                                  if garment else {})
        #: bone -> "garment" | "reference" for every centroid a direction
        #: used, "frameAxis" where no centroid was (the head)
        self.centroid_source: dict = {}
        #: bone -> HOW `rest_direction` read it: "hipMid->spine",
        #: "frameAxis", "parentLine", "joint->joint(c)", "joint->centroid(c)"
        #: or "joint->ownCentroid" (the leaf proxy); `foot_rest_direction`
        #: overwrites a foot's with "sole: ..." when it re-derives it
        self.direction_how: dict = {}
        # +Z is DOWN in C3; the sole is the mesh's largest z (-0.067 on the
        # shape-3 v_body; the manifest's referenceMesh.floorZ is the number).
        self.floor_z = max((v.position[2] for v in mesh.vertices),
                           default=0.0) if mesh else 0.0

    #: The bone tables the directions read (`RigTables`); None = the p84
    #: constants. Set by `retarget_clip` after construction -- an attribute,
    #: not a constructor argument, so the constructor's text stays what the
    #: shapes-2/4 branch (director/retarget-shapes-2-4) also writes.
    tables = None

    def add_attach(self, b, p) -> bool:
        """Make bone `b` rigid with `p` (the same matrix) where the rig
        leaves `b` without a parent. True when applied; False when refused
        OUT LOUD (`attach_refused` + a note) because the rig already parents
        `b`; a `RetargetError` when `p` is not a rig bone."""
        b, p = int(b), int(p)
        if p not in self.parents:
            raise RetargetError("--attach %d:%d names a parent the rig "
                                "does not have" % (b, p))
        # "The rig already places it" is a NON-None parent, not "a key
        # of rig.parents": the pre-#200 rig keys bone 65 as a root with
        # value None, and testing `b in self.parents` skipped
        # `--attach 65:64` -- same bytes as no attach, 65 carried, the
        # flap playing the original dance under the donor's pelvis.
        q = self.parents.get(b)
        if q is not None:
            self.attach_refused[b] = {"asked": p, "rigParent": q}
            self.notes.append(
                "--attach %d:%d not applied: the rig already parents %d "
                "on %d%s" % (b, p, b, q,
                             " (redundant)" if q == p else
                             "; the rig's parent is used"))
            return False
        self.parents[b] = p
        self.attach[b] = p
        return True

    def head_joint(self, b: int):
        p = self.parents.get(b)
        if p is None:
            return None
        return self.joints.get(joint_key(p, b))

    def direction_centroid(self, b: int):
        """The centroid a DIRECTION may end at: the garment's where it skins
        `b`, else the reference mesh's, else None. Records the source."""
        c = self.garment_centroids.get(b)
        if c is not None:
            self.centroid_source[b] = "garment"
            return tuple(c)
        c = self.centroids.get(b)
        if c is not None:
            self.centroid_source[b] = "reference"
            return tuple(c)
        return None

    def foot_points(self, bones=None) -> list:
        """``[(bind point, bone)]`` -- every vertex of the reference mesh and
        the garment whose dominant bone is one of `bones` (default: the
        tables' `foot_bones`). What `hover` poses."""
        if bones is None:
            bones = (self.tables or P84_TABLES).foot_bones
        pts = []
        for mesh in (self.mesh, self.garment):
            if mesh is None:
                continue
            for v in mesh.vertices:
                b = int(v.bone0)
                if b in bones and float(v.weight0) >= 0.5:
                    pts.append((tuple(v.position), b))
        return pts

    def sole_heading(self, b: int):
        """The SOLE of foot bone `b`, read off the mesh its direction
        centroid comes from (the garment where it skins `b`, else the
        reference mesh): the dominant-bone vertices within `SOLE_BAND_UNITS`
        of the lowest (+Z is down), the forward-most of them (-Y is the
        front: the toe tip), the rearmost (the heel), and the HORIZONTAL
        unit heading from the ankle joint to the tip. None when the foot
        has no ankle joint, skins nothing, or the tip sits under the ankle.

        What `foot_rest_direction` turns into a rest direction: the heading
        is the one thing the mesh has to say about a toeless foot that the
        own-centroid proxy got wrong by up to 49 deg (Maud/Pedlar's boot
        centroid sits far to the side of the ankle)."""
        head = self.head_joint(b)
        if head is None:
            return None
        mesh = (self.garment if (self.garment is not None and b in self.garment_centroids)
                else self.mesh)
        if mesh is None:
            return None
        fv = [v for v in mesh.vertices if int(v.bone0) == b and float(v.weight0) >= 0.5]
        if not fv:
            return None
        lowest = max(v.position[2] for v in fv)
        band = [v for v in fv if lowest - v.position[2] < SOLE_BAND_UNITS]
        tip = min(band, key=lambda v: v.position[1])
        heel = max(band, key=lambda v: v.position[1])
        h = heading(v_sub(tuple(tip.position), head))
        if h is None:
            return None
        return {"heading": h, "tip": [round(float(x), 3) for x in tip.position],
                "heel": [round(float(x), 3) for x in heel.position],
                "soleZ": round(float(lowest), 3), "band": len(band), "footVertices": len(fv),
                "ankleAboveSole": round(float(lowest - head[2]), 3),
                "soleLength": round(float(heel.position[1] - tip.position[1]), 3),
                "source": "garment" if mesh is self.garment else "reference"}

    def pelvis_pivot(self, root: int = 1) -> tuple:
        c = self.centroids.get(root)
        if c is None:
            raise RetargetError("no bind centroid for root bone %d" % root)
        return tuple(c)

    def pelvis_height(self, root: int = 1) -> float:
        return abs(self.pelvis_pivot(root)[2] - self.floor_z)

    def rest_direction(self, b: int) -> tuple:
        """Joint-to-joint where the rig has both joints; head joint to the
        direction child's centroid where the child has no joint (feet ->
        toes); the bind frame's axis for a bone in `C3_FRAME_AXIS` (the
        head: up); head joint to own centroid for any other leaf;
        hip-midpoint to the spine joint for the pelvis (`C3_HIP_CHILDREN`,
        NOT every child). The tables are `self.tables` (`RigTables`), the
        p84 constants unless `--map auto` derived them."""
        T = self.tables or P84_TABLES
        if self.parents.get(b) is None:
            j2 = self.joints.get(joint_key(b, T.direction_child.get(b, -1)))
            hips = T.hip_children.get(b)
            if hips is None:
                raise RetargetError("root bone %d has no hip children table" % b)
            legs = [self.joints.get(joint_key(b, k)) for k in hips]
            if j2 is None or any(j is None for j in legs):
                raise RetargetError("root bone %d lacks a spine or hip joint "
                                    "(%s)" % (b, [joint_key(b, k) for k in hips]))
            mid = tuple(sum(j[i] for j in legs) / len(legs) for i in range(3))
            self.direction_how[b] = "hipMid->spine"
            return v_sub(j2, mid)
        head = self.head_joint(b)
        if head is None:
            raise RetargetError("bone %d has no joint with its parent" % b)
        axis = T.frame_axis.get(b)
        if axis is not None:
            self.centroid_source[b] = "frameAxis"
            self.direction_how[b] = "frameAxis"
            return tuple(float(x) for x in axis)
        if b in T.direction_inherit:
            p = self.parents.get(b)
            if p is None or self.parents.get(p) is None:
                raise RetargetError("bone %d inherits its direction from %s, "
                                    "which has no parent line" % (b, p))
            self.centroid_source[b] = "parentLine"
            self.direction_how[b] = "parentLine"
            return v_sub(head, self.head_joint(p))
        child = T.direction_child.get(b)
        if child is not None:
            j2 = self.joints.get(joint_key(b, child))
            if j2 is not None:
                self.direction_how[b] = "joint->joint(%d)" % child
                return v_sub(j2, head)
            c2 = self.direction_centroid(child)
            if c2 is not None:
                self.direction_how[b] = "joint->centroid(%d)" % child
                return v_sub(c2, head)
            self.notes.append("bone %d points at %d but no mesh skins %d; "
                              "direction is head joint -> own centroid (the "
                              "proxy that pitched the feet 12 deg)"
                              % (b, child, child))
        c = self.direction_centroid(b)
        if c is None:
            raise RetargetError("bone %d is a leaf with no centroid" % b)
        self.direction_how[b] = "joint->ownCentroid"
        return v_sub(c, head)


# ---------------------------------------------------------------------------
# a toeless foot's rest direction: the sole, not the boot's centroid
# ---------------------------------------------------------------------------

#: The cut on the toeless-foot PROXY (ankle joint -> the foot's own mesh
#: centroid): how far its pitch below the horizontal may sit from the
#: donor's foot -> toe-base pitch (32.2 / 32.7 deg on this donor) before the
#: foot's rest direction is re-derived from the SOLE (`foot_rest_direction`).
#:
#: WHY THERE IS A RULE. `rest_offset=direction` aligns the c3 rest direction
#: onto the donor's at the donor's rest pose, so the sole ends up tilted by
#: exactly (proxy pitch - donor pitch): a boot whose centroid sits deep
#: under the ankle reads 55-72 deg and comes out toes-UP by 23-40 deg.
#: MEASURED 2026-10-03 on the 2026-10-03 set (pitch - donor, L/R deg):
#: Boxer +0.3/+1.1, Guard 900 +2.9/+1.7, Pharmacist +10.9/+9.5 (all three
#: ship with toes 5-10 units up, the owner saw the Guard's) | 9992670
#: +22.6/+27.1, 153 +26.1/+24.5, Maud/Pedlar +39.4/+37.6 (toes 16-29 units
#: off the ground on donor-flat frames; withdrawn by the geometry pass).
#: 15 sits in the gap: 4.1 above the worst kept, 7.6 below the first
#: refused. NO MESH POINT GIVES THE DONOR'S PITCH BY ITSELF: the sole tip
#: reads it within 2.5 deg on 153 and Maud and misses by 10 on the Boxer
#: and 9992670; the ball of the foot (72% of the sole) the reverse; the
#: ratio ankle-height / sole-length runs 0.33 (Boxer) to 0.56 (153). So
#: over the cut the PITCH is the donor's -- both rest poses stand on their
#: soles, which is the shared reference a toeless foot has -- and only the
#: HEADING is the mesh's (`Target.sole_heading`), and the correction is a
#: pure yaw (`yaw_rotation_between`), so the sole is flat wherever the
#: donor's is. Under the cut the proxy stands, and the file keeps its bytes.
FOOT_PROXY_PITCH_TOL_DEG = 15.0

#: The sole band: foot vertices within this many c3 units of the lowest
#: one (`Target.sole_heading`). The geometry pass measured soles with the
#: same 3.0; the soles here are 23-52 units long.
SOLE_BAND_UNITS = 3.0


def pitch_deg(d) -> float:
    """Degrees BELOW the horizontal in c3 (+Z is down): + = pointing down."""
    return math.degrees(math.atan2(d[2], math.hypot(d[0], d[1])))


def heading(d):
    """The horizontal unit vector under `d`, or None when `d` is vertical."""
    h = math.hypot(d[0], d[1])
    if h < 1e-9:
        return None
    return (d[0] / h, d[1] / h, 0.0)


def yaw_rotation_between(a, b):
    """The rotation ABOUT THE VERTICAL taking `a`'s heading to `b`'s:
    `rotation_between` on the two headings, which lie in the XY plane, so
    the axis is +-Z exactly and the pitch is untouched. With `a` and `b`
    at one pitch it maps unit(a) onto unit(b) like `rotation_between`
    would, without the roll the shortest arc between two pitched vectors
    carries (sin p . cos p . dyaw: 5-6 deg at 32 deg pitch and 13 deg of
    heading, measured on the Guard's proxy)."""
    ha, hb = heading(a), heading(b)
    if ha is None or hb is None:
        raise RetargetError("yaw_rotation_between: a vertical direction has no heading")
    return rotation_between(ha, hb)


def foot_rest_direction(tgt: Target, b: int, d_g) -> tuple:
    """``(c3 rest direction, C, info)`` for a mapped FOOT bone `b` against
    the donor's rest direction `d_g` (c3 axes).

    A foot whose direction `Target.rest_direction` reads from a toe joint
    or a toe centroid is left exactly as it was (p84's 44/49 -> 45/50; 126
    and 129's toes). A TOELESS foot -- direction ``joint->ownCentroid`` --
    keeps that proxy when its pitch is within `FOOT_PROXY_PITCH_TOL_DEG`
    of the donor's (Guard, Boxer, Pharmacist: bytes unchanged) and is
    otherwise re-derived: the donor's pitch on the sole-tip heading, with
    a yaw-only correction (the module constant says why). `info` records
    which, with the numbers; the rule is also written into
    ``tgt.direction_how[b]``."""
    d_c = tgt.rest_direction(b)
    how = tgt.direction_how.get(b)
    info = {"rule": how, "donorPitchDeg": round(pitch_deg(d_g), 3),
            "proxyPitchDeg": round(pitch_deg(d_c), 3)}
    if how != "joint->ownCentroid":
        return d_c, rotation_between(d_g, d_c), info
    off = pitch_deg(d_c) - pitch_deg(d_g)
    info["proxyPitchMinusDonorDeg"] = round(off, 3)
    info["cutDeg"] = FOOT_PROXY_PITCH_TOL_DEG
    if abs(off) <= FOOT_PROXY_PITCH_TOL_DEG:
        info["kept"] = True
        return d_c, rotation_between(d_g, d_c), info
    sole = tgt.sole_heading(b)
    if sole is None:
        info["kept"] = True
        info["note"] = ("the proxy is %+.1f deg off the donor's pitch but the foot has "
                        "no sole heading; the proxy stands" % off)
        tgt.notes.append("foot %d: %s" % (b, info["note"]))
        return d_c, rotation_between(d_g, d_c), info
    th = math.radians(pitch_deg(d_g))
    h = sole["heading"]
    d_new = (h[0] * math.cos(th), h[1] * math.cos(th), math.sin(th))
    C = yaw_rotation_between(d_g, d_new)
    check = angle_between_deg(m3_apply(C, v_unit(d_g)), d_new)
    rule = ("sole: the donor's pitch (%.1f deg) on the sole-tip heading, yaw-only "
            "correction; the own-centroid proxy read %.1f deg (%+.1f off, over %g)"
            % (pitch_deg(d_g), pitch_deg(d_c), off, FOOT_PROXY_PITCH_TOL_DEG))
    tgt.direction_how[b] = "sole"
    info.update(rule=rule, kept=False, sole=sole,
                headingDeg=round(math.degrees(math.atan2(h[0], -h[1])), 3),
                donorHeadingDeg=round(math.degrees(math.atan2(d_g[0], -d_g[1])), 3),
                yawOnlyCheckDeg=round(check, 6))
    if check > 1e-3:
        raise RetargetError("foot %d: the yaw-only correction misses the sole direction "
                            "by %.4f deg" % (b, check))
    return d_new, C, info


# ---------------------------------------------------------------------------
# the tables: the p84 constants, or the same tables DERIVED from labels
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field as _field                # noqa: E402


@dataclass(frozen=True)
class RigTables:
    """Every bone-index table `retarget_clip` consults, as ONE value.

    `P84_TABLES` is the nine hand-written constants above (shape 3's,
    measured 2026-09-30). `derive_tables` builds the same shape from
    `core/bonelabel.label_tree` labels on a loaded rig -- and on the p84 rig
    the two must be EQUAL, field for field (`tests/test_c3retarget_npcs.py`
    is the oracle for the whole derivation). A monster or NPC rig gets its
    tables derived, never the p84 numbers.
    """
    mapping: dict                 # Mixamo base name -> c3 bone
    fingers: frozenset            # bones rigid with their hand unless --fingers
    frame_axis: dict              # bone -> up axis (the head)
    direction_child: dict         # bone -> child whose joint gives direction
    hip_children: dict            # root -> (thigh.L, thigh.R)
    attach: dict                  # bone -> parent, for bones the rig lacks
    foot_bones: tuple             # feet and toes: what `hover` poses
    pelvis: int = PELVIS
    source: str = "p84 constants"
    labels: dict = _field(default_factory=dict)      # bone -> label, when derived
    notes: tuple = ()
    #: bones whose rest direction is their PARENT's line (elbow joint ->
    #: wrist joint): a hand with no finger child. Its centroid is a fist's,
    #: and points anywhere (Guard 900: 36 and 43 deg, asymmetric); the
    #: donor's own `Hand -> HandIndex1` lies within 1.2 deg of its forearm
    #: line (module docstring), so the forearm IS the hand's rest direction
    #: on both sides. Empty on p84, whose hands point at their index fingers.
    direction_inherit: frozenset = frozenset()

    def describe(self) -> dict:
        return {"source": self.source,
                "pelvis": self.pelvis,
                "mapping": {n: b for n, b in sorted(self.mapping.items(),
                                                    key=lambda kv: kv[1])},
                "fingers": sorted(self.fingers),
                "frameAxis": {str(b): list(a) for b, a in sorted(self.frame_axis.items())},
                "directionChild": {str(b): c for b, c in sorted(self.direction_child.items())},
                "hipChildren": {str(b): list(c) for b, c in sorted(self.hip_children.items())},
                "attach": {str(b): p for b, p in sorted(self.attach.items())},
                "footBones": list(self.foot_bones),
                "directionInherit": sorted(self.direction_inherit),
                "labels": {str(b): l for b, l in sorted(self.labels.items())},
                "notes": list(self.notes)}

    def same_as(self, other) -> list:
        """The field names on which `self` and `other` differ -- empty when
        the two would drive `retarget_clip` identically."""
        out = []
        for f in ("mapping", "frame_axis", "direction_child", "hip_children",
                  "attach", "foot_bones", "pelvis"):
            if getattr(self, f) != getattr(other, f):
                out.append(f)
        if set(self.fingers) != set(other.fingers):
            out.append("fingers")
        if set(self.direction_inherit) != set(other.direction_inherit):
            out.append("direction_inherit")
        return out


P84_TABLES = RigTables(mapping=dict(MAPPING), fingers=frozenset(FINGERS),
                       frame_axis=dict(C3_FRAME_AXIS),
                       direction_child=dict(C3_DIRECTION_CHILD),
                       hip_children=dict(C3_HIP_CHILDREN),
                       attach=dict(DEFAULT_ATTACH), foot_bones=tuple(FOOT_BONES),
                       pelvis=PELVIS, source="p84 constants")

#: Mixamo base name -> `core/bonelabel` label, where the label is a plain
#: lookup. The spine (chain length varies), the fingers (thumb vs index is
#: geometry: the thumb's knuckle sits nearer the midline) and the toes (on
#: p84 they are garment-only bones the rig never saw) are resolved by
#: `derive_tables` from the rig, not from this dict.
MIXAMO_LABEL = {
    "Hips": "pelvis", "Neck": "neck", "Head": "head",
    "LeftShoulder": "shoulder.L", "LeftArm": "upper_arm.L",
    "LeftForeArm": "forearm.L", "LeftHand": "hand.L",
    "RightShoulder": "shoulder.R", "RightArm": "upper_arm.R",
    "RightForeArm": "forearm.R", "RightHand": "hand.R",
    "LeftUpLeg": "thigh.L", "LeftLeg": "shin.L", "LeftFoot": "foot.L",
    "RightUpLeg": "thigh.R", "RightLeg": "shin.R", "RightFoot": "foot.R",
}
#: The donor's spine from the pelvis UP; `Spine2` is the chest.
DONOR_SPINE = ("Hips", "Spine", "Spine1", "Spine2")

#: THE HUMANOID GATE. A target is retargeted only when every label the
#: mapping NEEDS is present exactly once. Neck and head are optional -- monster
#: 126 (ToughMonster, 17/18) labels a neck and no head and is one of the
#: three families this route was proved on; a bone with no donor counterpart
#: is simply rigid with its parent. The 16 required ones are the pelvis,
#: the chest and the seven limb names on both sides.
CORE_REQUIRED = ("pelvis", "chest",
                 "thigh.L", "shin.L", "foot.L", "thigh.R", "shin.R", "foot.R",
                 "shoulder.L", "upper_arm.L", "forearm.L", "hand.L",
                 "shoulder.R", "upper_arm.R", "forearm.R", "hand.R")
CORE_OPTIONAL = ("neck", "head")
#: An over-long chain: `bonelabel._name_chain` suffixes repeats past its name
#: list (`head_2`, `shin_2.L`). On the 59 shipping monster families these are
#: the tails and extra chains (155: head..head_5; 152: seven heads) -- the
#: detectable signature of a non-humanoid, together with a duplicated core
#: label (a tail labelled as a third leg: 103, 200, 205).
_EXTRA_CHAIN = re.compile(r"^(neck|head|thigh|shin|foot|shoulder|upper_arm|forearm|hand)_\d+(\.[LR])?$")
_FINGER = re.compile(r"^finger(_\d+)*(\.[LR])$")
_LEG_SIDE = re.compile(r"^(thigh|shin|foot|toe)(_\d+)?\.([LR])$")


def _kids(parents: dict) -> dict:
    kids: dict = {}
    for b, p in parents.items():
        if p is not None:
            kids.setdefault(int(p), []).append(int(b))
    for v in kids.values():
        v.sort()
    return kids


def _subtree(kids: dict, b: int) -> list:
    out, stack, seen = [], [int(b)], set()
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
        stack.extend(kids.get(x, ()))
    return out


def clean_labels(labels: dict, parents: dict) -> tuple:
    """``(labels, notes)``: `bonelabel.label_tree`'s labels with the SKIRT
    FLAPS taken out of the legs.

    `label_tree` names EVERY child of the pelvis a leg (`bonelabel.py`,
    "the legs off its far end"). On the p84 rig that is five children: the
    two legs, and the three skirt flaps 52/53, 64/65 (two-link chains at
    the midline, y +8.0 / -11.36) -- labelled `thigh.R`/`shin.R` three times
    over (MEASURED on the fresh master solve, 2026-10-01). A leg is a chain
    that reaches a FOOT; a pelvis chain that never does is a flap and is
    relabelled ``flap_<n>`` (not a core label) so the humanoid gate sees the
    two real legs and the hip-children table reads (42, 47), as the
    hand-written one does. A tail with a foot (ape 103's 40-44, labelled
    thigh..toe_2) is NOT a flap and still fires the gate as a third leg.
    """
    labels = {int(b): str(l) for b, l in labels.items()}
    kids = _kids(parents)
    notes = []
    n = 0
    for b in sorted(labels):
        lab = labels.get(b)
        if lab is None:
            continue
        m = _LEG_SIDE.match(lab)
        if not m or m.group(1) != "thigh":
            continue
        side = m.group(3)
        chain = _subtree(kids, b)
        if any(labels.get(c, "").startswith("foot") and labels.get(c, "").endswith("." + side)
               for c in chain):
            continue
        n += 1
        for i, c in enumerate(sorted(chain, key=lambda c: chain.index(c))):
            if c in labels:
                labels[c] = "flap_%d" % n if i == 0 else "flap_%d_%d" % (n, i + 1)
        notes.append("pelvis chain %s labelled %s.%s reaches no foot: a flap, "
                     "not a leg (flap_%d)" % (chain, m.group(1), side, n))
    return labels, notes


def humanoid_verdict(labels: dict, optional=()) -> dict:
    """Is this skeleton one the mapping can drive? ``{"ok", "missing",
    "duplicates", "extra", "core", "reason"}`` -- every refusal names its
    labels, so a reader can see WHICH bone a tail or a second head is.

    `optional` names core labels the CALLER has measured absent for a
    stated reason, and the verdict tolerates their absence like the head's
    (``optionalMissing`` lists them, ``optional`` says they were asked
    for). The one caller is the skin-tree route: an arm whose first link
    sits at 0.35-0.49 of its lateral reach has NO CLAVICLE BONE
    (`geo_labels`, `GEO_CLAVICLE_CUT`), so ``shoulder.L``/``shoulder.R``
    cannot exist on it and the donor's upper arm drives its first link.
    `bonelabel.label_tree` names every arm's first link a shoulder, so no
    own-rig or lender verdict is changed by this argument (its default is
    empty, and the `core` count is "%d/18" on every path)."""
    inv: dict = {}
    for b, l in labels.items():
        inv.setdefault(str(l), []).append(int(b))
    optional = tuple(str(o) for o in (optional or ()))
    missing = [l for l in CORE_REQUIRED if l not in inv and l not in optional]
    dups = {l: sorted(bs) for l, bs in sorted(inv.items())
            if len(bs) > 1 and (l in CORE_REQUIRED or l in CORE_OPTIONAL)}
    extra = {l: sorted(bs) for l, bs in sorted(inv.items()) if _EXTRA_CHAIN.match(l)}
    core = sum(1 for l in CORE_REQUIRED + CORE_OPTIONAL if l in inv)
    parts = []
    if missing:
        parts.append("missing %s" % missing)
    if dups:
        parts.append("duplicated %s" % dups)
    if extra:
        parts.append("extra chain(s) %s" % extra)
    ok = not (missing or dups or extra)
    opt_missing = [l for l in CORE_OPTIONAL + optional if l not in inv]
    return {"ok": ok, "missing": missing, "duplicates": dups, "extra": extra,
            "core": "%d/%d" % (core, len(CORE_REQUIRED) + len(CORE_OPTIONAL)),
            "optionalMissing": opt_missing,
            "optional": list(optional),
            "reason": ("humanoid: %s core labels, no duplicates%s"
                       % (core, ("; %s optional, absent" % ", ".join(
                           l for l in optional if l not in inv))
                          if any(l not in inv for l in optional) else "") if ok
                       else "not a humanoid this mapping can drive: " + "; ".join(parts))}


def label_rig(rig, lateral: int = 0) -> dict:
    """`bonelabel.label_tree` on a `FamilyRig`: ``{bone: label}`` from its
    parents and joint points (no centroids: the joints decide)."""
    joints = {tuple(int(x) for x in k.split("-")): tuple(v)
              for k, v in (rig.joint_points or {}).items()}
    return bonelabel.label_tree(rig.parents, joints=joints, centroids=None,
                                lateral=lateral)


def _where(joints: dict, centroids: dict, a: int, b: int):
    """The position of the joint between `a` and `b` (the rig's ``"lo-hi"``
    table), else `b`'s centroid, else None."""
    p = joints.get(joint_key(a, b))
    if p is not None:
        return tuple(p)
    c = (centroids or {}).get(b)
    return tuple(c) if c is not None else None


#: THE ANATOMY GATE's cut on a labelled thigh's BIND direction (hip joint ->
#: knee joint) against DOWN (+Z). `humanoid_verdict` reads labels, and
#: labels are topology: `bonelabel` names the two longest chains off the
#: pelvis that reach a foot "legs" whichever way they point. Monster 161
#: passed 17/18 that way and is not a biped -- its labelled thighs are
#: horizontal limbs at mid-height and its real legs (bones 42-48) were
#: demoted to a flap and rode rigid with the pelvis (geometry verifier,
#: 2026-10-02: feet 58-109 units off the ground, the file withdrawn).
#: MEASURED on the CCO snapshot, hip->knee against +Z, L / R:
#:
#:     Guard 900        7.95 /  8.56      Pharmacist 9992720   7.29 /  7.27
#:     monster 153      5.45 /  8.73      Boxer 9992680       10.71 /  7.73
#:     monster 126     14.13 / 14.23      player p84 shape 3   5.59 /  5.57
#:     monster 129     24.24 / 24.24      player p84 shape 4  11.13 / 10.99
#:     monster 161     78.79 / 80.11   <- refused
#:
#: The most crouched humanoid (129, knees bent 55 deg in its bind) reads
#: 24.2; 161 reads 78.8. 45 sits 20.8 over the highest humanoid and 33.8
#: under 161 -- and is the angle past which a "thigh" is more sideways than
#: down.
THIGH_DOWN_CUT_DEG = 45.0


def anatomy_gate(rig, labels: dict, tables, *, mesh=None, garment=None) -> dict:
    """The GEOMETRIC half of the humanoid gate: three measurements the
    labels cannot make, each a refusal that names its numbers.

    1. ``thighFromDownDeg``: each labelled thigh's bind direction (hip joint
       -> knee joint) against +Z, refused over `THIGH_DOWN_CUT_DEG`. A thigh
       whose joints the rig lacks cannot be measured and is refused too.
    2. ``skinnedOutsideRig``: every bone the drawn mesh (or the garment)
       SKINS that the rig, re-hung from the pelvis, gives no parent and the
       tables do not attach. `solve_translations` leaves such a bone CARRIED
       from the source clip while the body moves away from it (161's bone
       25, vertex weight 12.0: a constant 22.7 from its neighbour in the
       source, 10.3-47.1 in the output). Skipped, and said, with no mesh.
    3. ``pelvis``: the labelled pelvis must be a rig bone, the ROOT once
       `Target` has re-hung the rig from it, and carry exactly two leg
       chains -- its children whose subtree reaches a foot must be thigh.L
       (reaching foot.L) and thigh.R (reaching foot.R) and no others.

    -> ``{"ok", "refusals": [...], "thighFromDownDeg", "skinnedOutsideRig",
    "pelvis", "cuts"}``. Run by `derive_tables` once the label gate passes.
    """
    labels = {int(b): str(l) for b, l in labels.items()}
    inv = {}
    for b, l in labels.items():
        inv.setdefault(l, []).append(b)
    one = {l: bs[0] for l, bs in inv.items() if len(bs) == 1}
    par0 = {int(k): (None if v is None else int(v)) for k, v in rig.parents.items()}
    joints = {k: tuple(float(x) for x in v) for k, v in (rig.joint_points or {}).items()}
    pel = int(tables.pelvis)
    out = {"ok": True, "refusals": [], "thighFromDownDeg": {}, "skinnedOutsideRig": {},
           "pelvis": {"bone": pel}, "cuts": {"thighFromDownDeg": THIGH_DOWN_CUT_DEG}}
    refuse = out["refusals"].append

    # -- 3. the pelvis: a rig bone, the root after the reroot, two leg chains
    if pel not in par0:
        refuse("the labelled pelvis (bone %d) is not a bone of the rig: nothing "
               "to re-hang the skeleton from" % pel)
        out["ok"] = False
        return out
    par = par0
    if par0.get(pel) is not None:
        old, guard = pel, 0
        while par0.get(old) is not None and guard <= len(par0):
            old, guard = par0[old], guard + 1
        par = {int(k): (None if v is None else int(v))
               for k, v in bonelabel._reroot_at(par0, pel).items()}
        out["pelvis"]["rerootedFrom"] = old
    out["pelvis"]["isRoot"] = par.get(pel) is None
    kids = _kids(par)
    chains = {}
    for c in kids.get(pel, ()):
        feet = sorted({labels[x] for x in _subtree(kids, c)
                       if labels.get(x) in ("foot.L", "foot.R")})
        if feet:
            chains[int(c)] = feet
    want = {}
    for side in ("L", "R"):
        th = one.get("thigh." + side)
        if th is not None:
            want[int(th)] = ["foot." + side]
    out["pelvis"]["legChains"] = {str(c): f for c, f in sorted(chains.items())}
    if par.get(pel) is not None or len(want) != 2 or chains != want:
        refuse("pelvis bone %d is not a root with two leg chains: its children "
               "that reach a foot are %s; a biped's are thigh.L %s -> foot.L and "
               "thigh.R %s -> foot.R, and nothing else"
               % (pel, {c: f for c, f in sorted(chains.items())} or "none",
                  one.get("thigh.L"), one.get("thigh.R")))

    # -- 1. the thighs hang
    bad = []
    for side in ("L", "R"):
        th, sh = one.get("thigh." + side), one.get("shin." + side)
        if th is None or sh is None:
            continue                      # the label gate's refusal, not this one's
        p = par.get(th)
        j0 = joints.get(joint_key(p, th)) if p is not None else None
        j1 = joints.get(joint_key(th, sh))
        if j0 is None or j1 is None or v_len(v_sub(j1, j0)) < 1e-9:
            out["thighFromDownDeg"]["thigh." + side] = None
            refuse("thigh.%s (bone %d) has no %s joint in the rig: its bind "
                   "direction cannot be measured" % (side, th, "hip" if j0 is None else "knee"))
            continue
        deg = angle_between_deg(v_sub(j1, j0), (0.0, 0.0, 1.0))
        out["thighFromDownDeg"]["thigh." + side] = round(deg, 2)
        if deg > THIGH_DOWN_CUT_DEG:
            bad.append("thigh.%s (bone %d) %.1f" % (side, th, deg))
    if bad:
        refuse("labelled thigh(s) point %s deg from DOWN in the bind pose (cut %g; "
               "the humanoids measured read 5.5-24.2): horizontal limbs, not the "
               "legs a biped stands on" % (" and ".join(bad), THIGH_DOWN_CUT_DEG))

    # -- 2. no skinned bone left carried
    if mesh is None and garment is None:
        out["skinnedOutsideRig"] = None
        out["pelvis"]["note"] = "no mesh given: skinned bones outside the rig not measured"
    else:
        mass: dict = {}
        for body in (mesh, garment):
            if body is None:
                continue
            for b, w in bonetree.bone_mass(body.vertices).items():
                mass[int(b)] = max(mass.get(int(b), 0.0), float(w))
        attach = {int(b) for b in (tables.attach or {})}
        outside = {b: round(w, 2) for b, w in sorted(mass.items())
                   if w > 0.0 and b != pel and par.get(b) is None and b not in attach}
        out["skinnedOutsideRig"] = {str(b): w for b, w in outside.items()}
        if outside:
            refuse("skinned bone(s) %s (vertex weight) have no parent in the rig and "
                   "no attach: they would be CARRIED from the source clip while the "
                   "body moves away from them"
                   % ", ".join("%d (%.1f)" % (b, w) for b, w in outside.items()))
    out["ok"] = not out["refusals"]
    return out


def derive_tables(rig, labels: dict = None, *, garment=None, mesh=None,
                  lateral: int = 0, optional=()) -> tuple:
    """``(RigTables, verdict, labels)`` -- every table `retarget_clip`
    consults, derived from `core/bonelabel` labels of `rig`.

    THE RULES, each one the hand-written p84 table spells for shape 3:

    * MAPPING: `MIXAMO_LABEL` for the limbs, pelvis, neck and head. The
      spine positionally from the pelvis UP (``pelvis, spine_k .. spine_1,
      chest`` against ``Hips, Spine, Spine1, Spine2``): a shorter c3 spine
      leaves the donor's middle names unmapped, a longer one leaves c3
      bones rigid with their parent. Fingers: of a hand's finger children,
      the THUMB is the knuckle nearest the midline (smallest |lateral| of
      the hand->finger joint) and the INDEX the furthest -- on p84 the
      `finger_N` numbers do not say which (12/14 are finger_1/2.L but
      22/20 are finger_1/2.R: `bonelabel` sorts both sides ascending in
      x). One finger: the hand's direction child, mapped as the index.
    * C3_DIRECTION_CHILD: each mapped bone points at the next bone up its
      own chain; the hand at its index finger; the foot at its toe (a
      labelled `toe.X`, or the garment-only bone attached below); a leaf
      (head excepted) at None, i.e. its own centroid.
    * C3_FRAME_AXIS: the head, up (0, 0, -1): +Z is down.
    * C3_HIP_CHILDREN: {pelvis: (thigh.L, thigh.R)}.
    * DEFAULT_ATTACH and the toes: a bone the GARMENT skins that the rig
      never saw (p84's toes 45 and 50: the reference body does not skin
      them, `docs/bone_labels_2026-09-21.md`) is attached to the nearest
      foot by centroid, provided it sits below that foot's shin joint
      (+Z down); it becomes the foot's direction child and a hover bone.
      No garment, no attach: a monster's feet are leaves.
    * FOOT_BONES: (foot.L, toe.L, foot.R, toe.R), whichever exist.
    * pelvis: the bone labelled pelvis.

    `labels` defaults to `label_rig(rig)`; `clean_labels` runs first (the
    skirt flaps). The verdict is `humanoid_verdict` on the cleaned labels
    (`optional`: core labels the caller measured absent, the skin-tree
    route's clavicle-less shoulders) and then, when the labels pass,
    `anatomy_gate` on the geometry (the thighs hang, no skinned bone is
    left carried, the pelvis is a root with two leg chains) --
    ``verdict["anatomy"]`` holds its measurements; a refused target still
    gets its (partial) tables back so a dry run can PRINT them beside the
    reason.
    """
    raw = dict(labels) if labels is not None else label_rig(rig, lateral)
    labels, notes = clean_labels(raw, rig.parents)
    verdict = humanoid_verdict(labels, optional=optional)
    inv: dict = {}
    for b, l in labels.items():
        inv.setdefault(l, []).append(int(b))
    one = {l: bs[0] for l, bs in inv.items() if len(bs) == 1}
    kids = _kids(rig.parents)
    joints = {k: tuple(v) for k, v in (rig.joint_points or {}).items()}
    cents = bonetree.bone_centroids(mesh.vertices) if mesh is not None else {}
    gcents = bonetree.bone_centroids(garment.vertices) if garment is not None else {}

    mapping: dict = {}
    for name, lab in MIXAMO_LABEL.items():
        if lab in one:
            mapping[name] = one[lab]
    # -- the spine, pelvis up ------------------------------------------------
    spine_up = []
    if "pelvis" in one:
        spine_up.append(one["pelvis"])
        ks = sorted((int(l.split("_")[1]), b) for l, b in one.items()
                    if re.match(r"^spine_\d+$", l))
        spine_up.extend(b for _k, b in sorted(ks, reverse=True))   # spine_k .. spine_1
    if "chest" in one:
        spine_up.append(one["chest"])
    if len(spine_up) >= 2:
        middle_c3 = spine_up[1:-1]
        middle_donor = list(DONOR_SPINE[1:-1])               # Spine, Spine1
        for dn, cb in zip(middle_donor, middle_c3):
            mapping[dn] = cb
        mapping[DONOR_SPINE[-1]] = spine_up[-1]
        if len(middle_c3) < len(middle_donor):
            notes.append("c3 spine has %d bone(s) between pelvis and chest; donor "
                         "%s unmapped" % (len(middle_c3), middle_donor[len(middle_c3):]))
        elif len(middle_c3) > len(middle_donor):
            notes.append("c3 spine has %d bones between pelvis and chest; %s rigid "
                         "with their parent" % (len(middle_c3), middle_c3[len(middle_donor):]))
    # -- fingers: thumb nearest the midline, index furthest ------------------
    fingers: set = set()
    hand_child: dict = {}
    for side, left in ((".L", "Left"), (".R", "Right")):
        hand = one.get("hand" + side)
        fs = sorted(b for b, l in labels.items() if _FINGER.match(l) and l.endswith(side))
        fingers.update(fs)
        if hand is None:
            continue
        first = [c for c in kids.get(hand, ()) if c in fs]
        lat = {}
        for c in first:
            w = _where(joints, gcents or cents, hand, c)
            if w is not None:
                lat[c] = abs(float(w[lateral]))
        if len(lat) >= 2:
            thumb = min(lat, key=lambda c: (lat[c], c))
            index = max(lat, key=lambda c: (lat[c], -c))
            mapping[left + "HandThumb1"] = thumb
            mapping[left + "HandIndex1"] = index
            hand_child[hand] = index
        elif len(first) == 1:
            mapping[left + "HandIndex1"] = first[0]
            hand_child[hand] = first[0]
            notes.append("one finger under hand%s (%d): mapped as the index, no "
                         "thumb" % (side, first[0]))
    # -- the toes: labelled, or garment-only bones below the feet -------------
    attach: dict = {}
    toe: dict = {}
    for side in (".L", ".R"):
        foot = one.get("foot" + side)
        if foot is None:
            continue
        t = one.get("toe" + side)
        if t is not None and rig.parents.get(t) == foot:
            toe[foot] = t
    if garment is not None and gcents:
        feet = [one[k] for k in ("foot.L", "foot.R") if k in one]
        orphans = sorted(b for b in gcents if b not in rig.parents and b not in labels)
        for b in orphans:
            if not feet:
                break
            cb = gcents[b]
            best = min(feet, key=lambda f: v_len(v_sub(cb, gcents.get(f) or cents.get(f) or cb)))
            shin = rig.parents.get(best)
            ankle = joints.get(joint_key(shin, best)) if shin is not None else None
            if ankle is not None and cb[2] < ankle[2]:
                notes.append("garment-only bone %d sits above the ankle of foot %d: not "
                             "a toe, left carried" % (b, best))
                continue
            if best in toe:
                notes.append("garment-only bone %d: foot %d already has toe %d; left "
                             "carried" % (b, best, toe[best]))
                continue
            attach[b] = best
            toe[best] = b
            notes.append("garment-only bone %d attached to foot %d (nearest foot by "
                         "centroid, below its ankle): the toe" % (b, best))
    # -- direction children ---------------------------------------------------
    direction: dict = {}
    for i, b in enumerate(spine_up):
        if b in mapping.values():
            if i + 1 < len(spine_up):
                direction[b] = spine_up[i + 1]
            else:
                direction[b] = one.get("neck")
    if "neck" in one:
        direction[one["neck"]] = one.get("head")
    if "head" in one:
        direction[one["head"]] = None
    for side in (".L", ".R"):
        arm = [one.get(n + side) for n in ("shoulder", "upper_arm", "forearm", "hand")]
        for a, nxt in zip(arm, arm[1:] + [None]):
            if a is None:
                continue
            direction[a] = nxt if nxt is not None else hand_child.get(a)
        leg = [one.get(n + side) for n in ("thigh", "shin", "foot")]
        for a, nxt in zip(leg, leg[1:] + [None]):
            if a is None:
                continue
            direction[a] = nxt if nxt is not None else toe.get(a)
    for b in list(mapping.values()):
        if b in fingers:
            direction[b] = None
    direction = {b: c for b, c in direction.items() if b in mapping.values()}
    # -- the rest -------------------------------------------------------------
    # The frame axis (up, +Z is down) for the TOP of the spine chain: the
    # head, or the neck when no head is labelled (monster 126: bone 6 is the
    # whole forward-jutting head, its centroid 94 deg off the donor's
    # Neck -> Head; as a frame axis its rest posture is kept and it plays
    # the donor's nods bare, which is the head's own rule).
    frame_axis = {}
    if "head" in one:
        frame_axis[one["head"]] = (0.0, 0.0, -1.0)
    elif "neck" in one:
        frame_axis[one["neck"]] = (0.0, 0.0, -1.0)
        notes.append("no head label: neck %d is the top of the spine and takes the "
                     "head's frame-axis rule (up), not its centroid" % one["neck"])
    inherit = set()
    for side in (".L", ".R"):
        hand = one.get("hand" + side)
        if hand is not None and hand in mapping.values() and hand not in hand_child                 and one.get("forearm" + side) == rig.parents.get(hand):
            inherit.add(hand)
            notes.append("hand%s (%d) has no finger: its rest direction is the "
                         "forearm line, not its centroid" % (side, hand))
    hip = {}
    if "pelvis" in one and "thigh.L" in one and "thigh.R" in one:
        hip[one["pelvis"]] = (one["thigh.L"], one["thigh.R"])
    foot_bones = tuple(b for b in (
        one.get("foot.L"), toe.get(one.get("foot.L")),
        one.get("foot.R"), toe.get(one.get("foot.R"))) if b is not None)
    pelvis = one.get("pelvis", PELVIS)
    if "pelvis" not in one:
        notes.append("no pelvis label: pelvis defaults to bone %d" % PELVIS)
    T = RigTables(mapping=mapping, fingers=frozenset(fingers), frame_axis=frame_axis,
                  direction_child=direction, hip_children=hip, attach=attach,
                  foot_bones=foot_bones, pelvis=int(pelvis),
                  source="labels (core/bonelabel.label_tree on the rig)",
                  labels=dict(labels), notes=tuple(notes),
                  direction_inherit=frozenset(inherit))
    # The labels are topology; the anatomy gate measures. Run only when the
    # labels pass: its three readings are OF the labelled bones.
    if verdict["ok"]:
        gate = anatomy_gate(rig, labels, T, mesh=mesh, garment=garment)
        verdict["anatomy"] = gate
        if not gate["ok"]:
            verdict["ok"] = False
            verdict["reason"] = ("not a humanoid this mapping can drive: "
                                 + "; ".join(gate["refusals"]))
    return T, verdict, labels


# ---------------------------------------------------------------------------
# geometric labels: a T-posed biped named from its tree AND its bind geometry
# ---------------------------------------------------------------------------

#: GEOMETRIC LABELS (the skin-tree route, 2026-10-03). `bonelabel.label_tree`
#: reads TOPOLOGY alone -- the spine is the branch that forks, the arms the
#: two widest, the neck what is left, the legs every chain off the pelvis --
#: and on the rigidly skinned NPC bodies the rig-library analysis
#: (2026-10-02, seven town families, 191 npc.json rows) measured four ways
#: that reads decoration as anatomy: a hat or hair chain above the head
#: becomes ``head_2..head_5`` (an extra chain); a three-link robe panel off
#: the pelvis a second leg (a duplicate); an arm with NO CLAVICLE BONE is
#: named shoulder/upper_arm/forearm/hand one bone early, so the donor's
#: shoulder drives the upper arm and its hand the fingers (MOTION x-sign
#: agreement 0.49 on 9990050, the lasso read right in 42 of 68 frames
#: against 67-68 everywhere else); twin braids off the head out-fork the
#: legs and the body is labelled upside down (999006100). These rules read
#: the bind geometry beside the tree -- +Z is DOWN, `lateral` (x) is across
#: the body, the sole is the mesh's largest z -- and every cut below is a
#: fraction of the mesh HEIGHT, with the measurement that placed it:
#:
#: * `GEO_FLOOR_CUT`: a leg is a chain of three or more links whose lowest
#:   centroid sits within this of the sole. The labelled feet of the seven
#:   families read 0.0 of height above it (`posture.feetAboveSoleOverH`);
#:   a robe panel's end hangs at mid-shin or higher.
#: * `GEO_LATERAL_MIN`: a leg chain's mean |x| is at least this -- a
#:   midline robe panel that does reach the floor is not a leg.
#: * `GEO_ARM_REACH_MIN`: an arm chain reaches at least this far sideways.
#:   The chest is the hub whose mirrored pair of such chains spans WIDEST
#:   (`bonelabel.find_chest`'s own rule, kept).
#: * `GEO_MIDLINE_CUT`: the neck is the chest chain whose first centroid
#:   lies within this of the midline and above the chest.
#: * `GEO_CLAVICLE_CUT`: the first arm link's |x| over the arm's lateral
#:   reach. MEASURED: 0.18-0.23 on the Guard 900, the Pharmacist, the
#:   Boxer, p84 and 9992710 (every arm with a clavicle bone), 0.35-0.49 on
#:   the seven clavicle-less NPC bodies; 0.30 sits in the gap. Under it the
#:   first link is a shoulder; over it the chain starts at the upper arm
#:   and the two shoulder labels are reported OPTIONAL (`humanoid_verdict`).
#: * `GEO_HEAD_RISE_MIN`: the link after the neck is the head when it rises
#:   at least this above the neck and more than it runs sideways, and is
#:   not lighter in skin than the neck (999009100: bone 6 carries the skull
#:   and 7 the official's hat; labelled neck/head the donor's head drove
#:   the hat). A heavy top-of-spine bone with no head above it keeps the
#:   `neck` label on purpose: the chest's rest direction points at the
#:   neck joint (`derive_tables`), and a neck with no head takes the head's
#:   frame-axis rule.
GEO_FLOOR_CUT = 0.08
GEO_LATERAL_MIN = 0.02
GEO_ARM_REACH_MIN = 0.25
GEO_MIDLINE_CUT = 0.05
GEO_CLAVICLE_CUT = 0.30
GEO_HEAD_RISE_MIN = 0.03

#: The labels `geo_labels` can write for a leg and an arm, in chain order.
GEO_LEG = ("thigh", "shin", "foot", "toe")
GEO_ARM_CLAVICLE = ("shoulder", "upper_arm", "forearm", "hand", "finger")
GEO_ARM_NO_CLAVICLE = ("upper_arm", "forearm", "hand", "finger")


def _tree_adjacency(parents: dict) -> dict:
    adj: dict = {}
    for b, p in parents.items():
        adj.setdefault(int(b), set())
        if p is not None:
            adj.setdefault(int(p), set()).add(int(b))
            adj[int(b)].add(int(p))
    return adj


def _walk_chain(adj: dict, hub: int, first: int) -> tuple:
    """``(chain, past)``: from `hub` into `first` until a leaf or a fork;
    `past` is the branches beyond the chain's end (empty at a leaf)."""
    out, prev, cur = [first], hub, first
    while True:
        nxt = [n for n in sorted(adj.get(cur, ())) if n != prev]
        if len(nxt) != 1:
            return out, nxt
        prev, cur = cur, nxt[0]
        if cur in out:                              # a cycle cannot be walked
            return out, []
        out.append(cur)


def _tree_path(adj: dict, a: int, b: int):
    prev = {a: None}
    queue = [a]
    while queue:
        x = queue.pop(0)
        if x == b:
            break
        for y in sorted(adj.get(x, ())):
            if y not in prev:
                prev[y] = x
                queue.append(y)
    if b not in prev:
        return None
    out = [b]
    while prev[out[-1]] is not None:
        out.append(prev[out[-1]])
    return out[::-1]


def geo_labels(parents: dict, centroids: dict, height: float, floor_z: float,
               mass: dict = None, *, lateral: int = 0) -> tuple:
    """``(labels, notes, info)``: `bonelabel`-style labels for a biped from
    its tree AND its bind geometry (the module constants above say which
    rule placed each cut). Unlabelled bones are ABSENT, as in `label_tree`,
    and ride rigid with their parent in the retarget.

    `parents` is ``{bone: parent_or_None}`` (any rooting: the rules read
    the undirected tree), `centroids` ``{bone: bind centroid}`` of the
    drawn mesh, `height` its z extent, `floor_z` its sole (max z), `mass`
    ``{bone: skin weight}`` (the hat rule). `info` carries every
    measurement: the pelvis and its leg symmetry, the chest and its span,
    each arm's reach and first-link ratio, the clavicle verdict per side,
    ``shouldersOptional`` (the shoulder labels the gate must not require),
    the neck/head decision, and the bones left unlabelled.
    """
    par = {int(b): (None if p is None else int(p)) for b, p in parents.items()}
    cents = {int(b): tuple(float(x) for x in c) for b, c in (centroids or {}).items()}
    mass = {int(b): float(w) for b, w in (mass or {}).items()}
    h = float(height)
    adj = _tree_adjacency(par)
    notes: list = []
    info: dict = {"cuts": {"floor": GEO_FLOOR_CUT, "lateralMin": GEO_LATERAL_MIN,
                           "armReachMin": GEO_ARM_REACH_MIN, "midline": GEO_MIDLINE_CUT,
                           "clavicle": GEO_CLAVICLE_CUT, "headRiseMin": GEO_HEAD_RISE_MIN},
                  "height": round(h, 3), "floorZ": round(float(floor_z), 3),
                  "pelvis": None, "chest": None, "spine": [], "legs": {}, "arms": {},
                  "clavicle": {}, "shouldersOptional": [], "neck": None, "head": None,
                  "unlabelled": []}
    if h <= 1e-9 or not adj:
        notes.append("no mesh height or no tree: nothing to label")
        info["unlabelled"] = sorted(adj)
        return {}, notes, info

    def x_of(b):
        return cents[b][lateral]

    # ---- the pelvis: the hub with exactly one floor-reaching lateral chain a side
    best = None
    ambiguous = []
    for hub in sorted(adj):
        legs = []
        for n in sorted(adj[hub]):
            chain, _past = _walk_chain(adj, hub, n)
            if len(chain) < 3:
                continue
            known = [b for b in chain if b in cents]
            if not known:
                continue
            end = max(known, key=lambda b: cents[b][2])          # +Z is down: the lowest
            above = float(floor_z) - cents[end][2]
            if above > GEO_FLOOR_CUT * h:
                continue
            mx = sum(x_of(b) for b in known) / len(known)
            if abs(mx) < GEO_LATERAL_MIN * h:
                continue
            legs.append((mx, chain, above))
        left = [c for c in legs if c[0] > 0]
        right = [c for c in legs if c[0] < 0]
        if len(left) == 1 and len(right) == 1:
            score = abs(left[0][0] + right[0][0])
            if best is None or score < best[0]:
                best = (score, hub, left[0], right[0])
        elif left and right:
            ambiguous.append((hub, len(left), len(right)))
    for hub, nl, nr in ambiguous:
        notes.append("hub %d has %d + %d floor-reaching lateral chains: ambiguous legs"
                     % (hub, nl, nr))
    if best is None:
        notes.append("no hub with exactly one floor-reaching lateral chain on each side "
                     "(a leg: 3+ links, lowest centroid within %g of height above the sole, "
                     "mean |x| at least %g of height): no legs found" % (GEO_FLOOR_CUT, GEO_LATERAL_MIN))
        info["unlabelled"] = sorted(adj)
        return {}, notes, info
    _score, pelvis, (xl, leg_l, above_l), (xr, leg_r, above_r) = best
    info["pelvis"] = {"bone": pelvis, "legSymmetryUnits": round(_score, 3),
                      "legMeanXOverHeight": {"L": round(xl / h, 4), "R": round(xr / h, 4)},
                      "footAboveSoleOverHeight": {"L": round(above_l / h, 4),
                                                  "R": round(above_r / h, 4)}}
    info["legs"] = {"L": list(leg_l), "R": list(leg_r)}

    # ---- the chest: the hub whose mirrored pair of wide chains spans widest
    cbest = None
    for hub in sorted(adj):
        if hub == pelvis:
            continue
        arms = []
        for n in sorted(adj[hub]):
            chain, past = _walk_chain(adj, hub, n)
            if len(chain) < 3:
                continue
            xs = [x_of(b) for b in chain if b in cents]
            if not xs:
                continue
            reach = max(xs, key=abs)
            if abs(reach) < GEO_ARM_REACH_MIN * h:
                continue
            arms.append((reach, chain, past))
        left = [a for a in arms if a[0] > 0]
        right = [a for a in arms if a[0] < 0]
        if left and right:
            l_arm = max(left, key=lambda a: a[0])
            r_arm = min(right, key=lambda a: a[0])
            span = l_arm[0] - r_arm[0]
            if cbest is None or span > cbest[0]:
                cbest = (span, hub, l_arm, r_arm)
    if cbest is None:
        notes.append("no hub with a mirrored pair of 3+ link chains reaching %g of height "
                     "sideways: no arms found" % GEO_ARM_REACH_MIN)
        info["unlabelled"] = sorted(adj)
        return {}, notes, info
    span, chest, arm_l, arm_r = cbest
    info["chest"] = {"bone": chest, "armSpanOverHeight": round(span / h, 4)}
    spine = _tree_path(adj, pelvis, chest)
    if spine is None:
        notes.append("pelvis %d and chest %d are in different components" % (pelvis, chest))
        info["unlabelled"] = sorted(adj)
        return {}, notes, info
    limb_bones = set(leg_l) | set(leg_r) | set(arm_l[1]) | set(arm_r[1])
    if set(spine) & limb_bones:
        notes.append("the pelvis -> chest path %s runs through a limb" % spine)
        info["unlabelled"] = sorted(adj)
        return {}, notes, info
    info["spine"] = list(spine)

    label: dict = {pelvis: "pelvis", chest: "chest"}
    middle = spine[1:-1]                                 # pelvis side first
    for i, b in enumerate(reversed(middle)):             # spine_1 is next to the chest
        label[b] = "spine_%d" % (i + 1)
    for side, chain in ((".L", leg_l), (".R", leg_r)):
        for b, nm in zip(chain, GEO_LEG):
            label[b] = nm + side
        if len(chain) > len(GEO_LEG):
            notes.append("leg%s has %d links; %s unlabelled (rigid with the toe)"
                         % (side, len(chain), chain[len(GEO_LEG):]))
    for side, (reach, chain, past) in ((".L", arm_l), (".R", arm_r)):
        x0 = abs(x_of(chain[0])) if chain[0] in cents else 0.0
        ratio = x0 / abs(reach) if abs(reach) > 1e-9 else 1.0
        clavicle = ratio < GEO_CLAVICLE_CUT
        names = GEO_ARM_CLAVICLE if clavicle else GEO_ARM_NO_CLAVICLE
        info["arms"][side[1:]] = {"chain": list(chain), "reachOverHeight": round(abs(reach) / h, 4),
                                  "firstLinkOverReach": round(ratio, 4), "clavicle": clavicle,
                                  "fingerBranches": list(past)}
        info["clavicle"][side[1:]] = round(ratio, 4)
        notes.append("arm%s %s: first link at %.2f of the reach -> %s"
                     % (side, chain, ratio,
                        "a clavicle (shoulder first)" if clavicle
                        else "NO clavicle bone (upper_arm first; shoulder%s optional)" % side))
        if not clavicle:
            info["shouldersOptional"].append("shoulder" + side)
        for i, b in enumerate(chain):
            if i < len(names):
                label[b] = names[i] + side
            else:
                label[b] = "finger_%d%s" % (i - len(names) + 2, side)
        if past and label.get(chain[-1], "").startswith("hand"):
            ordered = sorted(past, key=lambda c: x_of(c) if c in cents else 0.0)
            for n, c in enumerate(ordered, 1):
                sub, _p2 = _walk_chain(adj, chain[-1], c)
                for i, d in enumerate(sub):
                    label[d] = ("finger_%d%s" % (n, side) if i == 0
                                else "finger_%d_%d%s" % (n, i + 1, side))

    # ---- neck / head: the chest chain that climbs the midline
    cand = []
    for n in sorted(adj[chest]):
        if n in label or n not in cents:
            continue
        chain, _past = _walk_chain(adj, chest, n)
        c = cents[n]
        if abs(c[lateral]) < GEO_MIDLINE_CUT * h and c[2] < cents[chest][2]:
            cand.append((len(chain), -c[2], chain))
    if cand:
        if len(cand) > 1:
            notes.append("%d midline chains above the chest: %s; the longest taken as neck/head"
                         % (len(cand), [c[2] for c in cand]))
        chain = max(cand)[2]
        neck = chain[0]
        label[neck] = "neck"
        info["neck"] = neck
        named = 1
        if len(chain) > 1 and chain[1] in cents:
            a, b = cents[neck], cents[chain[1]]
            rise = a[2] - b[2]                           # +Z down: positive = higher
            horiz = math.hypot(b[0] - a[0], b[1] - a[1])
            m0, m1 = mass.get(neck, 0.0), mass.get(chain[1], 0.0)
            head_info = {"candidate": chain[1], "riseOverHeight": round(rise / h, 4),
                         "runOverHeight": round(horiz / h, 4),
                         "neckMass": round(m0, 2), "candidateMass": round(m1, 2)}
            if mass and m0 > m1:
                notes.append("link %d (skin mass %.0f) sits above %d (mass %.0f): a hat or "
                             "topknot, not a head -- %d is the top of the spine"
                             % (chain[1], m1, neck, m0, neck))
                head_info["verdict"] = "hat: lighter than the neck"
            elif rise > GEO_HEAD_RISE_MIN * h and rise > horiz:
                label[chain[1]] = "head"
                named = 2
                head_info["verdict"] = "head"
                info["head"] = chain[1]
            else:
                notes.append("link %d after %d rises %.1f and runs %.1f sideways: hair or hat, "
                             "not a head -- %d is the top of the spine"
                             % (chain[1], neck, rise, horiz, neck))
                head_info["verdict"] = "hair: no rise"
            info["headRule"] = head_info
        if len(chain) > named:
            notes.append("links past the top of the spine %s are hair or hat: unlabelled, "
                         "rigid with their parent" % chain[named:])
    else:
        notes.append("no midline chain above the chest: no neck or head label")
    info["unlabelled"] = sorted(b for b in adj if b not in label)
    return label, notes, info


# ---------------------------------------------------------------------------
# frames and encodings
# ---------------------------------------------------------------------------

def resample_matrices(src, bone: int, n_out: int) -> list:
    """`bone`'s matrices of the source `Motion` at `n_out` frames: the
    source's first and last keys land on frames 0 and n_out-1 and the rest
    is the engine's own element-wise lerp (`Motion.matrix`, graphic.dll
    Motion_GetMatrix). What a CARRIED bone gets when `--frames` changes the
    count: the original clip is a valid clip, stretched."""
    keys = src.keys
    if not keys:
        return [tuple([1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0])] * n_out
    f0, f1 = float(keys[0].frame), float(keys[-1].frame)
    out = []
    for k in range(n_out):
        t = f0 + (f1 - f0) * (k / (n_out - 1) if n_out > 1 else 0.0)
        out.append(tuple(float(x) for x in src.matrix(bone, t)))
    return out


def _quat_of_m16(m) -> tuple:
    """``(qx, qy, qz, qw)`` of the rotation `m` stores (row-vector form), the
    inverse of `effects.quat_to_matrix` up to float noise."""
    R, _t = m16_to_col3(m)
    tr = R[0][0] + R[1][1] + R[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (R[2][1] - R[1][2]) / s
        y = (R[0][2] - R[2][0]) / s
        z = (R[1][0] - R[0][1]) / s
    elif R[0][0] > R[1][1] and R[0][0] > R[2][2]:
        s = math.sqrt(1.0 + R[0][0] - R[1][1] - R[2][2]) * 2
        w = (R[2][1] - R[1][2]) / s
        x = 0.25 * s
        y = (R[0][1] + R[1][0]) / s
        z = (R[0][2] + R[2][0]) / s
    elif R[1][1] > R[2][2]:
        s = math.sqrt(1.0 + R[1][1] - R[0][0] - R[2][2]) * 2
        w = (R[0][2] - R[2][0]) / s
        x = (R[0][1] + R[1][0]) / s
        y = 0.25 * s
        z = (R[1][2] + R[2][1]) / s
    else:
        s = math.sqrt(1.0 + R[2][2] - R[0][0] - R[1][1]) * 2
        w = (R[1][0] - R[0][1]) / s
        x = (R[0][2] + R[2][0]) / s
        y = (R[1][2] + R[2][1]) / s
        z = 0.25 * s
    return (x, y, z, w)


def encode_body(keys: list, bone_count: int, frame_count: int, encoding: str,
                extra_channels: int = 0, extra_payload: bytes = b"",
                trailing: bytes = b"") -> tuple:
    """``(Motion, encoding_written, note)``: the new body track in
    `encoding` if `effects.serialize_moti` can write it and `parse_moti`
    hands the SAME matrices back (bit-exact), else RAW.

    `keys` is ``[(frame, [m16 per bone])]`` with float32-rounded matrices.
    MEASURED (`tests/test_c3retarget_npcs.py`, the encoding arm): RAW and
    KKEY carry the matrices themselves and round-trip exact; XKEY stores
    the 4x3 rows and round-trips exact too (the fourth column is 0 0 0 1 by
    construction); ZKEY stores a quaternion and `quat_to_matrix` is lossy,
    so a ZKEY source comes back RAW and the manifest says ``encoding RAW
    (source ZKEY)``. The engine was proved (2026-10-01, Guard 900) to honour
    frameCount on a RAW body; any other encoding written at a new count is
    an UNVERIFIED engine acceptance and is flagged as such.
    """
    enc = str(encoding).upper()
    want = [[tuple(m) for m in mats] for _f, mats in keys]
    f7 = struct.Struct("<7f")

    def build(e):
        mk = []
        for f, mats in keys:
            src = []
            if e == "ZKEY":
                for m in mats:
                    q = _quat_of_m16(m)
                    src.append(f7.unpack(f7.pack(q[0], q[1], q[2], q[3],
                                                 m[12], m[13], m[14])))
            elif e == "XKEY":
                for m in mats:
                    src.append((m[0], m[1], m[2], m[4], m[5], m[6],
                                m[8], m[9], m[10], m[12], m[13], m[14]))
            mk.append(effects.MotionKey(int(f), [tuple(m) for m in mats], src))
        return effects.Motion(bone_count, frame_count, e, mk, extra_channels,
                              0, 0, extra_payload, trailing)

    if enc == "RAW":
        return build("RAW"), "RAW", "RAW (source RAW)"
    try:
        m = build(enc)
        back = effects.parse_moti(effects.serialize_moti(m))
        got = [[tuple(mm) for mm in k.matrices] for k in back.keys]
        exact = (got == want and [k.frame for k in back.keys] == [f for f, _ in keys])
    except Exception as e:                                   # noqa: BLE001
        exact = False
        note = "%s: %s" % (e.__class__.__name__, e)
    else:
        note = "round trip %s" % ("exact" if exact else "NOT exact")
    if exact:
        return m, enc, "%s (source %s; %s)" % (enc, enc, note)
    return build("RAW"), "RAW", "RAW (source %s: %s)" % (enc, note)


def _socket_const4(const):
    return ((const[0], const[1], const[2], const[3]),
            (const[4], const[5], const[6], const[7]),
            (const[8], const[9], const[10], const[11]),
            (const[12], const[13], const[14], const[15]))


def regenerate_sockets_resampled(chunks, slots, rig, motion, body_slot: int) -> tuple:
    """The 1-bone socket tracks at the NEW body's key count: ``socket_k =
    constant . body_k`` for every key `k`, in the socket's own encoding when
    it round-trips (`encode_body`), else RAW.

    `c3rig.regenerate_sockets` matches keys BY INDEX against the socket's
    existing key list, which is right while the body keeps its count and
    wrong once `--frames` changes it (a 20-key socket would follow the first
    20 of 302 body frames and then stop). Used only then. -> ``(new_bodies,
    rows, carried, warnings)``; `carried` names every socket slot left
    byte-for-byte because it has no solved host.
    """
    new_bodies: dict = {}
    rows: list = []
    carried: list = []
    warnings: list = []
    consts = rig.socket_constants or {}
    for ordinal, slot in enumerate(slots):
        if slot == body_slot:
            continue
        try:
            sock = effects.parse_moti(chunks[slot][1])
        except Exception as e:                               # noqa: BLE001
            warnings.append("socket %d unreadable: %s; carried" % (ordinal, e))
            carried.append({"ordinal": ordinal, "why": "unreadable"})
            continue
        if int(sock.bone_count) != 1:
            carried.append({"ordinal": ordinal, "why": "%d-bone track, not a socket"
                            % sock.bone_count})
            continue
        meta = consts.get(str(ordinal)) or {}
        host = meta.get("host")
        const = meta.get("constant") or []
        if host is None or len(const) != 16 or int(host) >= int(motion.bone_count):
            carried.append({"ordinal": ordinal, "encoding": sock.encoding,
                            "keys": len(sock.keys), "frameCount": sock.frame_count,
                            "why": meta.get("why") or "no solved host for this ordinal"})
            warnings.append("socket %d (%s, %d keys, frameCount %d) has no solved "
                            "host; carried byte-for-byte while the body has %d "
                            "frames" % (ordinal, sock.encoding, len(sock.keys),
                                        sock.frame_count, motion.frame_count))
            continue
        c4 = _socket_const4(const)
        keys = []
        for k in motion.keys:
            h4 = bonerig._as4(k.matrices[int(host)])
            t4 = c3anim.mat_mul4(c4, h4)
            m16 = _f32(c3rig._flat16(t4))
            keys.append((int(k.frame), [m16]))
        s_motion, enc, note = encode_body(keys, 1, int(motion.frame_count), sock.encoding,
                                          sock.extra_channels, sock.extra_payload,
                                          sock.trailing)
        new_bodies[slot] = effects.serialize_moti(s_motion)
        rows.append({"ordinal": ordinal, "host": int(host), "keys": len(keys),
                     "encodingBefore": sock.encoding, "encoding": enc,
                     "encodingNote": note, "keysBefore": len(sock.keys),
                     "frameCountBefore": sock.frame_count,
                     "frameCount": int(motion.frame_count)})
    return new_bodies, rows, carried, warnings


def reference_mesh_for(ar, target, data: bytes, mesh_path: str = None) -> tuple:
    """``(mesh, how)``: the body the directions, the pelvis pivot and the
    floor are read from, for a `rigtarget.RigTarget`.

    `mesh_path` (``--mesh``) first. Then the target's DRAWN mesh (`ini/3dobj.ini`
    for a monster, `3DSimpleObj.ini -> 3dobj.ini` for an NPC): on the
    Pharmacist's standby `c3/npc/999272100.c3` the clip's own `v_body` is a
    THREE-vertex stub on bone 4 -- the copy the client never draws
    (`core/npcart`) -- while `c3/npc/9992720.c3` skins 22 bones over 851
    vertices, and the rig was solved on the latter. Then the clip's own
    `v_body`, which is the player path (the clip carries the reference
    body) and the 30 monster families whose standby IS the drawn mesh.
    """
    if mesh_path:
        m = bonerig.reference_mesh_from_file(ar, mesh_path)
        if m is None:
            raise RetargetError("--mesh %s has no skinned PHY" % mesh_path)
        return m, "--mesh %s" % mesh_path
    if target is not None and target.kind != rigtarget.PLAYER and target.drawn_mesh:
        m = bonerig.reference_mesh_from_file(ar, target.drawn_mesh)
        if m is not None:
            return m, "drawn mesh %s (%s)" % (target.drawn_mesh, target.drawn_mesh_how)
    m = reference_mesh_from_clip(data)
    if m is not None:
        return m, "the target clip's own v_body PHY"
    return None, "none: no skinned v_body in the target%s" % (
        " and %s has no skinned PHY" % target.drawn_mesh
        if target is not None and target.drawn_mesh else "")


def body_slot_of(chunks, slots) -> tuple:
    """``(ordinal, bone_count)`` of the BODY track in a container: the MOTI
    with the most bones, as `bonerig.family_files` decides it. A rig's
    `body_ordinal` is a property of the containers it was solved from (3 on
    shape 3, after three sockets); Sage's standby and every monster's hold
    the body at 0 with no sockets."""
    best = None
    for ordinal, slot in enumerate(slots):
        body = chunks[slot][1]
        if len(body) < 12:
            continue
        (bc,) = struct.unpack_from("<I", body, 0)
        if best is None or int(bc) > best[1]:
            best = (ordinal, int(bc))
    if best is None:
        raise RetargetError("target carries no MOTI chunk")
    return best


# ---------------------------------------------------------------------------
# the retarget
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# followers and the borrowed player route (player-bodied NPCs)
# ---------------------------------------------------------------------------

#: A bone is RIGID with a host when ``M_bone . M_host^-1`` is one constant
#: on every key read -- the socket test (`bonerig._socket_constants`), and
#: its tolerance.
FOLLOW_RIGID_TOLERANCE = bonerig.SOCKET_TOLERANCE


def _flat16(m4) -> tuple:
    return tuple(float(m4[r][c]) for r in range(4) for c in range(4))


def bone_adjacency(body) -> dict:
    """``{bone: {bones}}``: bones whose vertices share a FACE of `body`, or
    blend on one vertex. The mesh's own statement of which piece hangs off
    which: a cape strip's triangles join consecutive strip bones, and its
    top row blends 50/50 with the chest."""
    adj: dict = {}
    vb = []
    for v in body.vertices:
        s = {int(v.bone0)}
        if float(getattr(v, "weight1", 0.0)) > 0.0:
            s.add(int(v.bone1))
        vb.append(s)
        for a in s:
            adj.setdefault(a, set()).update(s - {a})
    for face in (getattr(body, "faces", None) or ()):
        s = set()
        for i in face:
            if 0 <= int(i) < len(vb):
                s |= vb[int(i)]
        for a in s:
            adj.setdefault(a, set()).update(s - {a})
    return adj


def derive_followers(parents: dict, pelvis: int, motions: list, bodies: list) -> tuple:
    """``(follow, rows, unresolved)``: a HOST for every bone the drawn body
    skins that the rig does not place.

    A borrowed player rig places the player's bones; the NPC wearing it
    skins more (Sage: 23 -- the toes, six flap bones, and three five-bone
    cape strips 24-28 / 30-34 / 36-40). `solve_translations` would leave
    each CARRIED from the source clip, standing where the idle left it while
    the body dances away. A follower instead keeps its SOURCE transform
    RELATIVE TO ITS HOST (`retarget_clip`, ``follow``): where the source
    holds it rigid that is a constant, where the source sways it (the cape
    tips) the sway is kept, about the dancing host.

    The host, in order:

    * RIGID: the placed bone it is rigid with on every key of every family
      clip (`FOLLOW_RIGID_TOLERANCE`; the least spread wins). Sage's flap
      bones 56/60/68/72/76/80 read 2e-06..7e-06 against 52/64/65.
    * ADJACENT: else the nearest placed-or-rigid bone through the mesh's
      own adjacency (`bone_adjacency`: shared faces, blended vertices), and
      that bone's host. Sage's strips reach the chest (bone 4) through the
      50/50 vertices of 24/30/36.

    `rows` records host, how, the best rigid spread and the path;
    `unresolved` lists skinned bones with neither -- a caller must refuse
    those, not carry them."""
    par = {int(k): (None if v is None else int(v)) for k, v in parents.items()}
    placed = sorted({b for b, p in par.items() if p is not None} | {int(pelvis)})
    mass: dict = {}
    adj: dict = {}
    for body in bodies:
        if body is None:
            continue
        for b, w in bonetree.bone_mass(body.vertices).items():
            mass[int(b)] = mass.get(int(b), 0.0) + float(w)
        for a, s in bone_adjacency(body).items():
            adj.setdefault(a, set()).update(s)
    todo = sorted(b for b, w in mass.items() if w > 0.0 and b not in placed)
    follow: dict = {}
    rows: dict = {}
    if not todo:
        return follow, rows, []
    # -- rigid with a placed bone on every key of every clip -----------------
    spread = {b: {} for b in todo}
    first = {b: {} for b in todo}
    n_keys = 0
    for mo in motions:
        for key in mo.keys:
            n_keys += 1
            inv = {}
            for q in placed:
                try:
                    inv[q] = c3anim.mat_inv4(bonerig._as4(key.matrices[q]))
                except (c3anim.AnimEditError, ZeroDivisionError):
                    continue                    # a collapsed bone hosts nothing
            for b in todo:
                mb = bonerig._as4(key.matrices[b])
                for q in placed:
                    if q not in inv:
                        spread[b][q] = float("inf")
                        continue
                    c = _flat16(c3anim.mat_mul4(mb, inv[q]))
                    c0 = first[b].setdefault(q, c)
                    d = max(abs(x - y) for x, y in zip(c, c0))
                    # every candidate is RECORDED, an exact 0.0 included: a
                    # bone that equals its host to the last bit must rank
                    # (the hermetic fixture; on real clips the noise is 1e-6)
                    if d > spread[b].setdefault(q, 0.0):
                        spread[b][q] = d
    for b in todo:
        ranked = sorted(spread[b].items(), key=lambda kv: (kv[1], kv[0])) if n_keys else []
        best = ranked[0] if ranked else None
        rows[b] = {"host": None, "how": None, "weight": round(mass[b], 2),
                   "rigidSpread": round(best[1], 9) if best and best[1] != float("inf") else None,
                   "rigidNearest": best[0] if best else None, "keys": n_keys}
        if best is not None and best[1] <= FOLLOW_RIGID_TOLERANCE:
            follow[b] = int(best[0])
            rows[b].update(host=int(best[0]),
                           how="rigid with bone %d on %d keys of %d clip(s) (spread %.3g)"
                               % (best[0], n_keys, len(motions), best[1]))
    # -- else through the mesh: the nearest placed or rigid bone --------------
    unresolved = []
    for b in todo:
        if b in follow:
            continue
        seen, frontier, hit = {b}, [(b, [b])], None
        while frontier and hit is None:
            nxt = []
            for node, path in frontier:
                for nb in sorted(adj.get(node, ())):
                    if nb in seen:
                        continue
                    seen.add(nb)
                    if nb in placed:
                        hit = (nb, path + [nb])
                        break
                    if rows.get(nb, {}).get("how", "") and str(rows[nb]["how"]).startswith("rigid"):
                        hit = (follow[nb], path + [nb, follow[nb]])
                        break
                    if nb in todo:
                        nxt.append((nb, path + [nb]))
                if hit is not None:
                    break
            frontier = nxt
        if hit is None:
            unresolved.append(b)
            rows[b]["how"] = "no host: rigid with no placed bone and joined to none by the mesh"
            continue
        follow[b] = int(hit[0])
        rows[b].update(host=int(hit[0]), path=hit[1],
                       how="adjacent: the mesh joins it to bone %d through %s; its "
                           "source motion relative to %d is kept"
                           % (hit[0], hit[1], hit[0]))
    return follow, {str(b): r for b, r in sorted(rows.items())}, unresolved


def borrowed_route(ar, target, root, *, max_frames=bonerig.DEFAULT_MAX_FRAMES,
                   test: dict = None):
    """The PLAYER ROUTE for a player-bodied NPC, or None when the NPC is not
    one (`rigtarget.player_body` -- the test and its measured cuts).

    -> ``{"shape", "rig", "rigFile", "mesh", "meshHow", "garment",
    "garmentHow", "follow", "followers", "unresolved", "test", "why"}``:

    * the rig is the PLAYER's for the shape whose joints the NPC's own
      clips keep connected ("borrowed: player shape N");
    * the reference body is the PLAYER's too: the pelvis pivot and the
      root-motion scale are read off the reference body's pelvis centroid,
      and Sage's own (a robe skinned to bone 1 down to the hem) reads 54.1
      against the player's 106.6 -- half the bounce, about a pivot at knee
      height (measured 2026-10-02: scale 30.0 against 59.16);
    * the NPC's drawn mesh is the GARMENT -- the body that wears the clip,
      which is what that argument has meant since the toes;
    * `follow` hosts every bone the NPC skins that the player rig does not
      place (`derive_followers`); `unresolved` must be empty or the caller
      refuses.

    The tables are the player's (`tables=None`), so the humanoid gate has
    nothing to derive: the borrowed rig IS the proof."""
    test = test if test is not None else rigtarget.player_body(
        target, root, assets=ar, max_frames=max_frames)
    if not test.get("borrow"):
        return None
    shape = str(test["borrow"])
    rig = bonerig.load(test["rig"])
    ref = test.get("referenceMesh") or (rig.solved_from or {}).get("referenceMesh")
    mesh = reference_mesh_from_clip(ar.read(ref)) if ref else None
    if mesh is None:
        raise RetargetError("borrowed player shape %s: its reference body %r has "
                            "no skinned v_body" % (shape, ref))
    drawn = bonerig.reference_mesh_from_file(ar, target.drawn_mesh)
    if drawn is None:
        raise RetargetError("borrowed player shape %s: the NPC's drawn mesh %s has "
                            "no skinned PHY" % (shape, target.drawn_mesh))
    drawn_how = "the NPC's drawn mesh %s (%s)" % (target.drawn_mesh, target.drawn_mesh_how)
    # THE GARMENT IS THE PLAYER'S, NOT THE NPC'S MESH (2026-10-03). Through
    # 2026-10-02 the drawn mesh was the garment, so a toe centroid of the
    # Sage's (a floor-length robe over the feet) gave feet 44/49 their
    # direction: rest offsets 20.65/21.28 against the shape-3 build's 3.77/
    # 3.97, soles pitched +19/+20 deg toes-down, heels 11-12 units up, and
    # the same mesh under that build's own matrices stood flat (+1.7/+2.8,
    # the geometry pass's control). The reference body skins no toes, so
    # garment=None is no better (-12 deg, measured). The player's default
    # garment for the shape is the body the owner-verified build wore, and
    # the NPC's mesh is the skin that rides the result: it names the
    # followers' bones (`derive_followers`) and its feet are measured
    # (`retarget_clip(drawn=...)`, ``hover.drawn``), and that is all.
    gpath, ghow = default_garment(shape, ar)
    garment = None
    if gpath:
        try:
            garment = garment_mesh(ar.read(gpath))
        except Exception:                                    # noqa: BLE001
            garment = None
    if garment is not None:
        garment_how = ("the player's %s (%s): the body the owner-verified shape-%s build "
                       "wore; its toes give the feet their direction. %s is the skin "
                       "and the followers' body only" % (gpath, ghow, shape, drawn_how))
    else:
        gpath = ""
        garment = drawn
        garment_how = ("%s -- the player's default garment is not readable here (%s), so "
                       "the drawn mesh supplies the direction centroids as before "
                       "2026-10-03" % (drawn_how, ghow))
    attach, _src = default_attach(shape)
    tgt = Target(rig, mesh, attach, garment=garment)
    motions = [m for _p, m in rigtarget._body_motions(ar, target.paths)
               if int(m.bone_count) == int(rig.bone_count)]
    follow, rows, unresolved = derive_followers(tgt.parents, PELVIS, motions, [drawn])
    return {"shape": shape, "rig": rig, "rigFile": str(test["rig"]),
            "mesh": mesh, "meshHow": "borrowed: player shape %s reference body %s" % (shape, ref),
            "garment": garment, "garmentHow": garment_how, "garmentPath": gpath or None,
            "drawn": drawn, "drawnHow": drawn_how,
            "follow": follow, "followers": rows, "unresolved": unresolved,
            "test": test, "why": test["why"]}


def skin_tree_route(ar, target, root, *, test: dict = None, stitched_tol: float = None) -> dict:
    """The SKIN-TREE ROUTE (2026-10-03): the family's own skeleton read off
    its DRAWN MESH and confirmed by its own clips, then labelled by
    geometry and gated like every other rig.

    1. `rigtarget.skin_tree`: the tree from the mesh's face adjacency (two
       bones are adjacent when a face, a weld or a blended vertex joins
       vertices they skin), every seam judged by the family's own clips
       with the shipped solver's per-clip vote (`bonejoint.solve_from_clips`,
       the seam centroid as the hinge prior): CONFIRMED (the solved joint),
       UNCONFIRMED (no clip articulates the pair: the joint is the seam
       centroid, recorded as such) or VETOED (the clips contradict the
       seam: dropped from the tree). What the kept seams leave apart is
       bridged by proximity (`bonetree.link_components`) and recorded.
    2. `geo_labels` on that tree and the mesh's bind geometry.
    3. `derive_tables` -> `humanoid_verdict` + `anatomy_gate`, the SAME gate
       an own rig passes, with the shoulder labels optional only where the
       labeller measured no clavicle bone (`GEO_CLAVICLE_CUT`).
    4. Two refusals of this route's own: a MAPPED bone whose edge to its
       parent is a proximity bridge (a joint there would be a guess), and
       -- inside `rigtarget.skin_tree` -- the lender route's separation
       re-check on the family's own clips (`BORROW_SEPARATION_TOL`).

    -> ``{"ok", "why", "rig", "mesh", "tables", "labels", "verdict", "geo",
    "notes", "optional", "unconfirmedMapped", "bridgedMapped", "test"}``.
    `unconfirmedMapped` is the list a reader must take to the viewer: the
    labelled joints the clips never moved, placed at a mesh seam centroid,
    which only an eye can judge. `test` (a precomputed `rigtarget.skin_tree`
    result) lets the hermetic arms run the route on a synthetic mesh."""
    test = test if test is not None else rigtarget.skin_tree(target, root, assets=ar,
                                                             stitched_tol=stitched_tol)
    out = {"ok": False, "why": test.get("why", ""), "test": test, "rig": test.get("rig"),
           "mesh": test.get("mesh"),
           "meshHow": "drawn mesh %s (%s): the reference body AND the tree's source"
                      % (getattr(target, "drawn_mesh", None) or "(synthetic)",
                         getattr(target, "drawn_mesh_how", None) or "given"),
           "tables": None, "labels": {}, "verdict": None, "geo": None, "notes": [],
           "optional": [], "unconfirmedMapped": [], "bridgedMapped": []}
    if not test.get("ok"):
        out["why"] = "skin tree REFUSED: %s" % test.get("why", "")
        return out
    rig, mesh = test["rig"], test["mesh"]
    cents = bonetree.bone_centroids(mesh.vertices)
    mass = bonetree.bone_mass(mesh.vertices)
    floor_z = max(v.position[2] for v in mesh.vertices)
    h = float(test["height"])
    labels, notes, geo = geo_labels(rig.parents, cents, h, floor_z, mass)
    optional = tuple(geo.get("shouldersOptional") or ())
    T, verdict, labels2 = derive_tables(rig, labels=labels, mesh=mesh, optional=optional)
    out.update(tables=T, labels=labels2, verdict=verdict, geo=geo, notes=list(notes),
               optional=list(optional))
    if not verdict["ok"]:
        out["why"] = "skin tree REFUSED: %s%s" % (
            verdict["reason"], (" -- %s" % notes[-1]) if (not labels and notes) else "")
        return out
    # -- how each MAPPED bone's edge to its parent was placed ---------------
    pel = int(T.pelvis)
    par = {int(k): (None if v is None else int(v)) for k, v in rig.parents.items()}
    if par.get(pel) is not None:
        par = {int(k): (None if v is None else int(v))
               for k, v in bonelabel._reroot_at(par, pel).items()}
    how = test.get("edgeHow") or {}
    mapped = sorted(set(T.mapping.values()) - set(T.fingers))
    unconf, bridged = [], []
    for b in mapped:
        p = par.get(b)
        if p is None:
            continue
        k = joint_key(p, b)
        rec = {"bone": b, "label": labels2.get(b), "edge": k, "parent": p,
               "joint": [round(float(x), 3) for x in rig.joint_points.get(k, ())]}
        if how.get(k) == "unconfirmed":
            unconf.append(rec)
        elif how.get(k) == "bridged":
            bridged.append(rec)
    out["unconfirmedMapped"] = unconf
    out["bridgedMapped"] = bridged
    if bridged:
        out["why"] = ("skin tree REFUSED: mapped bone(s) %s hang on a PROXIMITY bridge -- no "
                      "face, weld or blended vertex joins them to their parent and no clip "
                      "articulates the pair, so a joint there would be a guess"
                      % ", ".join("%s (%s, edge %s)" % (r["bone"], r["label"], r["edge"])
                                  for r in bridged))
        return out
    res = test.get("residuals") or {}
    out["ok"] = True
    out["why"] = ("skin tree: %d seam(s) confirmed on the family's own clips (%d keys of %d "
                  "clip(s)), %d unconfirmed at the mesh seam (%d mapped: %s), %d vetoed, %d "
                  "bridged by proximity (none mapped); worst articulated edge %s separates "
                  "%s units = %.2f%% of height %.1f (cut %.1f%%); %d labels, %s%s"
                  % (test.get("confirmed", 0), test.get("keys", 0), test.get("clips", 0),
                     test.get("unconfirmed", 0), len(unconf),
                     ", ".join("%s(%s)" % (r["label"], r["bone"]) for r in unconf) or "none",
                     test.get("vetoed", 0), len(test.get("bridged") or []),
                     res.get("worstEdge"), res.get("worstSeparation"),
                     100.0 * (res.get("worstOverHeight") or 0.0), h,
                     100.0 * float((test.get("cuts") or {}).get("separationOverHeight", 0.0)),
                     len(labels2), verdict["reason"],
                     ("; no clavicle bone (first arm link at %s of the reach): the donor's "
                      "upper arm drives the first link" % "/".join(
                          "%.2f" % v for _s, v in sorted(geo["clavicle"].items())))
                     if optional else ""))
    return out


class RetargetResult:
    def __init__(self):
        self.bytes = None
        self.motion = None
        self.manifest: dict = {}
        self.warnings: list = []


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


_F32 = struct.Struct("<16f")


def _f32(m16) -> tuple:
    """The 16 floats as the float32 values the file will hold."""
    return _F32.unpack(_F32.pack(*m16))


def donor_time(k: int, fps_ms: float, duration: float, tail: str) -> float:
    """Target frame index -> donor clip time under the tail policy."""
    t = k * fps_ms / 1000.0
    if duration <= 0.0:
        return 0.0
    if t <= duration:
        return t
    if tail == "hold":
        return duration
    if tail == "loop":
        return math.fmod(t, duration)
    raise RetargetError("unknown --tail %r" % tail)


def retarget_clip(data: bytes, rig, donor: Donor, *, fps_ms: float = DEFAULT_FPS_MS,
                  tail: str = "loop", root_motion: str = "vertical",
                  rest_offset: str = "direction", attach: dict = None,
                  attach_source: str = None, assets=None,
                  fingers: bool = False, mesh=None, garment=None,
                  axis_map=None, progress=None,
                  tables: RigTables = None, frames: int = None,
                  body_ordinal: int = None, encoding: str = "keep",
                  ground: str = None, follow: dict = None,
                  hop_cap: float = None, drawn=None) -> RetargetResult:
    """One clip. Returns the new bytes, the new body `Motion`, the manifest
    pieces (conventionResidual, anatomy, hover, connectivity, counts,
    offsets, sockets) and warnings. `garment` is the wearing body's `v_body`
    PHY (`garment_mesh`), None for reference-mesh centroids throughout.
    `axis_map` overrides `AXIS_MAP` -- for the mirror CONTROL only, which
    runs the v1/v2 composition to show `anatomy.side` rejects it.

    `tables` (a `RigTables`) replaces the nine p84 constants -- the default
    IS them (`P84_TABLES`), so a call without it is the 2026-09-30 tool
    exactly; `--map auto` passes `derive_tables`' result. `frames` writes
    THAT many keys at `fps_ms` whatever the source clip holds (the Guard
    proof: 20 RAW frames -> 302, the engine honoured frameCount); None keeps
    the source's count, as before. `body_ordinal` names the body MOTI in
    THIS container; None = the MOTI with the most bones (`body_slot_of`),
    which is the rig's ordinal on every container it was solved from and 0
    on Sage's and the monsters'. `encoding` is ``keep`` (the source's where
    it round-trips bit-exact, `encode_body`) or ``raw``.

    `ground` (None, ``"mean"`` or ``"frame"``) adds a VERTICAL root term
    so the feet keep the donor's clearance (`GROUNDS`); None is the tool
    as it was, byte for byte. `follow` (``{bone: host}``,
    `derive_followers`) gives a bone the rig does not place its SOURCE
    transform relative to a host instead of carrying it absolute.

    `hop_cap` (None, or c3 units; `GROUND_HOP_CAP` is the builder's)
    clamps each frame's hover excess over the donor's clearance under
    `ground` (`ground_cap`); None leaves every `--ground` build as it was.
    `drawn` is the mesh the client DRAWS when it is not `mesh` or
    `garment` (the player route: the Sage's own body against the player's
    reference body): its feet are measured beside the hover the ground
    term used (``hover.drawn``) and never drive anything.

    A toeless mapped foot takes `foot_rest_direction`: the own-centroid
    proxy when its pitch is within `FOOT_PROXY_PITCH_TOL_DEG` of the
    donor's, else the donor's pitch on the sole-tip heading (``footRest``
    in the manifest says which, per foot, with the numbers).

    `attach` None means the SHAPE's default (`default_attach(rig.shape)`;
    shape 2's adds the hat host `7: 6`); a dict is the caller's and
    `attach_source` names it in the manifest ('user' from the CLI). With
    `assets` (an `AssetRoot`), a socket host the rig gives no place and
    the attach does not cover is DERIVED from the family's clips
    (`socket_host_outside_rig`) and attached when it equals a rig bone;
    either way such a host left carried is a WARNING, never silent."""
    A = AXIS_MAP if axis_map is None else tuple(tuple(float(x) for x in r)
                                                for r in axis_map)
    if tail not in TAILS:
        raise RetargetError("--tail must be one of %s" % (TAILS,))
    if root_motion not in ROOT_MOTIONS:
        raise RetargetError("--root-motion must be one of %s" % (ROOT_MOTIONS,))
    if rest_offset not in REST_OFFSETS:
        raise RetargetError("--rest-offset must be one of %s" % (REST_OFFSETS,))
    if str(encoding).lower() not in ("keep", "raw"):
        raise RetargetError("--encoding must be keep or raw")
    if ground not in (None, "") and ground not in GROUNDS:
        raise RetargetError("--ground must be one of %s" % (GROUNDS,))
    ground = ground or None
    T = tables if tables is not None else P84_TABLES
    res = RetargetResult()
    warn = res.warnings
    warn.extend(donor.warnings)

    chunks = list(c3phy.iter_chunks(data))
    slots = c3rig._moti_slots(chunks)
    if not slots:
        raise RetargetError("target carries no MOTI chunk")
    # THE BODY ORDINAL IS THE CONTAINER'S, not the rig's. `rig.body_ordinal`
    # is 3 on shape 3 (three sockets first) -- a property of the containers
    # the rig was solved from. NPC 901 Sage's standby holds the same 84-bone
    # body at ordinal 0 with no sockets, and the old `slots[rig.body_ordinal]`
    # refused it with "body ordinal 3 but only 1 MOTI chunk(s)". The body is
    # the MOTI with the most bones, as `bonerig.family_files` decides it.
    if body_ordinal is None:
        body_ord, _bc = body_slot_of(chunks, slots)
    else:
        body_ord = int(body_ordinal)
        if body_ord >= len(slots):
            raise RetargetError("body ordinal %d but only %d MOTI chunk(s)"
                                % (body_ord, len(slots)))
    if body_ord != int(rig.body_ordinal):
        warn.append("body track is MOTI ordinal %d in this container; the rig "
                    "was solved from containers binding it at %d (a layout "
                    "difference, not a skeleton one)" % (body_ord, rig.body_ordinal))
    body_slot = slots[body_ord]
    src = effects.parse_moti(chunks[body_slot][1])
    if int(src.bone_count) != int(rig.bone_count):
        raise RetargetError("body track is %d bones, the rig is %d"
                            % (src.bone_count, rig.bone_count))
    bone_count = int(src.bone_count)
    frame_count = int(src.frame_count)
    n_src = len(src.keys)
    if src.encoding == "RAW" and n_src != frame_count:
        raise RetargetError("RAW body with %d keys for frame_count %d"
                            % (n_src, frame_count))
    if frames is not None and int(frames) < 2:
        raise RetargetError("--frames must be >= 2, got %r" % (frames,))
    # `frames`: the key count to WRITE. None keeps the source's keys (RAW:
    # one per frame; KKEY: its own sparse list, carried frame numbers), and
    # the byte-length assertion below then holds. With `frames` the output
    # is dense -- key k at frame k, frame_count = frames -- and both lengths
    # are recorded instead.
    resample = frames is not None and (int(frames) != n_src or src.encoding != "RAW")
    if frames is None and src.encoding not in ("RAW", "KKEY"):
        raise RetargetError("target body track is %s; this tool writes RAW "
                            "and KKEY bodies at the source's key count "
                            "(ZKEY/XKEY need source tuples); give --frames to "
                            "write a dense track" % src.encoding)
    n_keys = int(frames) if frames is not None else n_src
    out_frame_count = int(frames) if frames is not None else frame_count

    if mesh is None:
        mesh = reference_mesh_from_clip(data)
    if mesh is None:
        raise RetargetError("no skinned v_body PHY in the target and no "
                            "reference mesh given")
    # The attach default: the SHAPE's table on the player route (`tables`
    # None -- shape 2's adds the hat host 7:6), the DERIVED tables' own on a
    # monster/NPC target or `--map auto` (the garment-only toes `derive_tables`
    # found; a monster's is usually empty). Never the player's per-shape
    # table on another skeleton: its bone numbers are p84's.
    if attach is None:
        if tables is None:
            attach, attach_source = default_attach(getattr(rig, "shape", None))
        else:
            attach, attach_source = dict(T.attach), "derived tables (--map auto)"
    elif not attach_source:
        attach_source = "caller"
    tgt = Target(rig, mesh, attach, garment=garment, pelvis=T.pelvis)
    tgt.tables = T
    attach_src = {b: attach_source for b in tgt.attach}

    # -- socket hosts the rig gives no place (shape 2's hat bone 7) ---------
    # Judged against the rig's OWN parents, before the attach: the manifest
    # then shows a host WAS outside and what covered it. One the attach does
    # not cover is derived from the family's clips when `assets` is given
    # (`socket_host_outside_rig`: attached when it EQUALS a rig bone's
    # matrix on every key read) and is a WARNING if it stays carried --
    # the next shape with this layout cannot pass silently.
    outside = socket_hosts_outside_rig(rig, tgt.rig_parents, pelvis=T.pelvis)
    socket_hosts = {"outsideRig": {str(h): o for h, o in sorted(outside.items())},
                    "derived": {}, "unattached": []}
    uncovered = [h for h in sorted(outside) if tgt.parents.get(h) is None]
    derived = (socket_host_outside_rig(rig, assets, parents=tgt.rig_parents,
                                       pelvis=T.pelvis)
               if uncovered and assets is not None else {})
    for h in uncovered:
        ords = "/".join(str(o) for o in outside[h])
        row = derived.get(h)
        if row is not None:
            socket_hosts["derived"][str(h)] = row
        if row is not None and row["attach"] is not None and tgt.add_attach(h, row["attach"]):
            how = ("max element %.3g over %d keys of %d clips"
                   % (row["spread"], row["keys"], row["clips"]))
            attach_src[h] = "derived: same matrix as bone %d (%s)" % (row["attach"], how)
            warn.append("socket %s host %d is outside the rig and the attach did "
                        "not cover it; attached to bone %d by derivation (same "
                        "matrix, %s) -- add %d:%d to DEFAULT_ATTACH_BY_SHAPE"
                        % (ords, h, row["attach"], how, h, row["attach"]))
            continue
        socket_hosts["unattached"].append(h)
        if row is not None:
            why = ("nearest rig bone %s at max element %.4g (runner-up %s), not within %g"
                   % (row["nearest"], row["spread"] or 0.0, row["runnerUp"], row["tolerance"]))
        else:
            why = "pass --attach %d:<bone> or give the run an asset root to derive one" % h
        warn.append("WARNING: socket %s host %d is outside the rig and not attached: "
                    "it is CARRIED from the vanilla clip and the socket will follow "
                    "it, not the retargeted body (shape 2's hat sat 67-108 units from "
                    "the head); %s" % (ords, h, why))

    # -- followers: bones the rig does not place, carried RELATIVE to a host
    follow_map: dict = {}
    for fb, fh in sorted((follow or {}).items()):
        fb, fh = int(fb), int(fh)
        if tgt.parents.get(fb) is not None or fb == T.pelvis:
            warn.append("follow %d:%d not applied: the rig already places bone %d"
                        % (fb, fh, fb))
            continue
        if fh != T.pelvis and tgt.parents.get(fh) is None:
            raise RetargetError("follow %d:%d names a host the rig does not place"
                                % (fb, fh))
        follow_map[fb] = fh

    # The mapping is the TABLES' when tables were given (derived for this
    # rig), else the donor's (the default MAPPING, or `mirrored_mapping()`
    # for the control). Either way every name must be a donor node.
    base_map = T.mapping if tables is not None else donor.mapping
    no_node = sorted(n for n in base_map if n not in donor.node_of)
    if no_node:
        raise RetargetError("donor has no node for mapped joint(s) %s" % no_node)
    mapping = {n: b for n, b in base_map.items()
               if fingers or b not in T.fingers}
    for n, b in mapping.items():
        if b not in tgt.parents:
            raise RetargetError("mapped bone %d (%s) is not in the rig" % (b, n))
    if T.pelvis not in tgt.parents or tgt.parents.get(T.pelvis) is not None:
        raise RetargetError("pelvis bone %d is not a root of the rig" % T.pelvis)
    if "Hips" not in mapping or "LeftHand" not in mapping:
        raise RetargetError("the mapping must drive Hips and LeftHand; it has %s"
                            % sorted(mapping))

    # -- rest offsets ---------------------------------------------------
    # C_b . unit(A . donor_rest_dir) = unit(c3_rest_dir)
    offsets: dict = {}
    c3_rest_dir: dict = {}

    def donor_dir(name, b, world):
        # the frame axis on BOTH sides wherever the c3 table says so (the
        # head on p84 -- `Donor.direction` already does that by name -- and
        # a headless neck under `--map auto`)
        if b in T.frame_axis and DONOR_FRAME_AXIS.get(name) is None:
            return donor.axis_direction(name, world)
        return donor.direction(name, world)
    foot_rest: dict = {}
    for name, b in mapping.items():
        d_g = axis_convert(donor_dir(name, b, donor.rest_world), A)
        if name in ("LeftFoot", "RightFoot"):
            d_c, C, info = foot_rest_direction(tgt, b, d_g)
            foot_rest[b] = dict(info, name=name)
        else:
            d_c = tgt.rest_direction(b)
            C = rotation_between(d_g, d_c)
        c3_rest_dir[b] = d_c
        offsets[b] = {"name": name, "C": C, "deg": rotation_angle_deg(C),
                      "donorRestDirC3": [round(x, 6) for x in v_unit(d_g)],
                      "c3RestDir": [round(x, 6) for x in v_unit(d_c)],
                      "how": tgt.direction_how.get(b)}
    warn.extend(tgt.notes)
    worst_b = max(offsets, key=lambda b: offsets[b]["deg"])

    # -- root motion scale ------------------------------------------------
    # `T.pelvis`, never the literal 1: `Target` has re-hung the rig from it
    # if `tree_from_joints` rooted the solve elsewhere (the chest on Guard
    # 900, monster 126 and the Pharmacist).
    hips_h = donor.hips_height()
    pelvis_h = tgt.pelvis_height(T.pelvis)
    scale = pelvis_h / hips_h if hips_h > 1e-9 else 0.0
    hips_rest = donor.rest_pos["Hips"]
    P1 = tgt.pelvis_pivot(T.pelvis)
    if tgt.rerooted:
        warn.append("rig was rooted at bone %d; re-hung from the pelvis (bone %d) "
                    "for the retarget" % (tgt.rerooted["wasRootedAt"], T.pelvis))

    # -- anatomy, rest pose: the checks the axis-map probe cannot do --------
    # (module docstring, THE PROBE IS DEGENERATE). FRONT: the donor's foot ->
    # toe segment mapped through A must point the way the c3 foot -> toe
    # direction does. SIDE, keyed on the GAME and not on a label: the bone
    # that hosts the `v_l_weapon` socket -- its ORDINAL read off this
    # target's PHY names (`socket_ordinal`; it is 1 on shapes 1/3/4 and 2 on
    # shape 2) -- is the character's LEFT hand (or a finger rigid with it,
    # shape 4's thumb 12); the donor's LeftHand must be mapped to it
    # (`mapping_ok`), and the donor LeftHand's REST position mapped through
    # A must have the sign of that bone's centroid x.
    front = {}
    for name in ("LeftFoot", "RightFoot"):
        b = mapping.get(name)
        if b is not None and b in c3_rest_dir:
            front[str(b)] = round(angle_between_deg(
                axis_convert(donor.rest_direction(name), A), c3_rest_dir[b]), 3)
    left_ord, left_ord_how = socket_ordinal(data, LEFT_WEAPON_PHY, rig)
    right_ord, right_ord_how = socket_ordinal(data, RIGHT_WEAPON_PHY, rig)
    left_bone, left_how = left_hand_bone(rig, left_ord)
    if left_bone is None:
        warn.append("anatomy.side cannot be keyed on the game: %s (%s)"
                    % (left_how, left_ord_how))
    right_bone = None
    if right_ord is not None:
        right_bone = ((rig.socket_constants or {}).get(str(right_ord)) or {}).get("host")
        right_bone = None if right_bone is None else int(right_bone)
    hand_c = None
    hand_c_src = None
    if left_bone is not None:
        hand_c = tgt.garment_centroids.get(left_bone)
        hand_c_src = "garment" if hand_c is not None else None
        if hand_c is None:
            hand_c = tgt.centroids.get(left_bone)
            hand_c_src = "reference" if hand_c is not None else None
    # No weapon socket (46 of 59 monster families, every NPC measured): the
    # side cannot be keyed on the game. The convention -- +x is the
    # character's LEFT -- is CARRIED from the player rig, the owner decides
    # by eye on the first install, and MOTION is measured on the bone the
    # mapping calls the left hand so the composition is at least
    # self-consistent. `side.carried` says which case this is.
    side_carried = False
    if left_bone is None and mapping.get("LeftHand") is not None:
        left_bone = mapping["LeftHand"]
        side_carried = True
        left_how = ("CARRIED: no weapon socket on this rig; bone %d is the "
                    "mapped LeftHand under the player's +x = LEFT convention, "
                    "which nothing here can assert" % left_bone)
        warn.append("+x = the character's LEFT is CARRIED from the player; "
                    "MOTION is measured on the mapped LeftHand (bone %d)" % left_bone)
        hand_c = tgt.garment_centroids.get(left_bone)
        hand_c_src = "garment" if hand_c is not None else None
        if hand_c is None:
            hand_c = tgt.centroids.get(left_bone)
            hand_c_src = "reference" if hand_c is not None else None
    left_x_donor = axis_convert(donor.rest_pos["LeftHand"], A)[0]
    on_left_bone = [n for n, b in mapping.items() if b == left_bone]
    side = {"keyedOn": left_how,
            "socketOrdinal": left_ord,
            "socketOrdinalFrom": left_ord_how,
            "rightSocketOrdinal": right_ord,
            "rightHandBone": right_bone,
            "leftHandBone": left_bone,
            "leftHandBoneRigParent": tgt.parents.get(left_bone) if left_bone is not None else None,
            "donorJointMappedToIt": on_left_bone[0] if on_left_bone else None,
            # `mapping_ok`: host == the mapped hand, or a FINGER the rig hangs
            # off it (shape 4's thumb 12 under hand 11); a mirrored mapping
            # fails both arms because 12's parent is 11, not 19.
            "mappingOk": mapping_ok(left_bone, mapping.get("LeftHand"), tgt.parents),
            "donorLeftHandRestXc3": round(left_x_donor, 4),
            "c3LeftHandCentroidX": round(hand_c[0], 3) if hand_c else None,
            "centroidSource": hand_c_src,
            "agree": (hand_c is not None
                      and (left_x_donor > 0) == (hand_c[0] > 0))}
    side["carried"] = side_carried
    side["ok"] = bool(side["agree"] and side["mappingOk"])
    if side_carried:
        # a carried side cannot be "ok" -- nothing keyed it; it can only be
        # self-consistent (`agree`: the mapped left hand sits on +x)
        side["ok"] = None

    # -- per frame ----------------------------------------------------------
    keys = []
    kinds_seen: dict = {}
    max_resid = 0.0
    max_resid_at = None
    resid = {b: [0.0, 0.0, 0] for b in mapping.values()}   # sum, max, n
    frames_in_donor = 0
    root_deltas = []
    hand_agree = 0                          # MOTION: left-hand x sign vs donor's
    foot_pts = tgt.foot_points()
    hover = []                              # lowest posed foot point above floor
    donor_hover = []
    # the DRAWN mesh's own feet, when it is neither the reference body nor
    # the garment (the player route): measured beside `hover`, against its
    # own sole, and never used for anything
    drawn_pts = []
    drawn_floor = tgt.floor_z
    if drawn is not None and drawn is not mesh and drawn is not garment:
        drawn_pts = [(tuple(v.position), int(v.bone0)) for v in drawn.vertices
                     if int(v.bone0) in T.foot_bones and float(v.weight0) >= 0.5]
        drawn_floor = max((v.position[2] for v in drawn.vertices), default=tgt.floor_z)
    drawn_hover = []
    donor_floor = min(donor.rest_pos[n][1] for n in donor.hover_joints)
    # CARRIED bones under `--frames`: the source clip stretched to the new
    # count by the engine's own lerp (`resample_matrices`), computed once per
    # bone, lazily, for the bones the solve leaves without a matrix.
    carried_src: dict = {}

    def carried(b, k):
        if not resample:
            return tuple(float(x) for x in src.keys[k].matrices[b])
        if b not in carried_src:
            # float32 like every solved matrix: the lerp is in doubles and
            # a KKEY/XKEY body only round-trips bit-exact on file floats
            carried_src[b] = [_f32(m) for m in resample_matrices(src, b, n_keys)]
        return carried_src[b][k]

    def frame_pose(k):
        """``(world, rotations, d)`` for target frame `k`: the donor's pose,
        every mapped bone's rotation and the root delta BEFORE grounding."""
        world = donor.pose(donor_time(k, fps_ms, donor.duration, tail))
        rotations: dict = {}
        for name, b in mapping.items():
            Rw = donor.rotation(name, world)
            D = m3_mul(Rw, m3_T(donor.rest_rot[name]))      # world delta
            Dp = conjugate_by_axis(D, A)                    # in c3 axes
            C = offsets[b]["C"]
            if rest_offset == "none":
                R = Dp
            elif rest_offset == "direction":
                R = m3_mul(Dp, m3_T(C))                     # D' . C^-1
            else:
                R = m3_mul(C, m3_mul(Dp, m3_T(C)))          # C . D' . C^-1
            rotations[b] = R
        d = v_scale(axis_convert(v_sub(donor.position("Hips", world),
                                       hips_rest), A), scale)
        if root_motion == "inplace":
            d = (0.0, 0.0, 0.0)
        elif root_motion == "vertical":
            d = (0.0, 0.0, d[2])
        return world, rotations, d

    def donor_hover_at(world):
        return (min(donor.position(n, world)[1] for n in donor.hover_joints)
                - donor_floor) * scale

    # -- GROUNDING (`--ground`): a vertical root term from the hover gap ----
    # hover_c3(k) - hover_donor(k) is how far the lowest posed foot point
    # sits above (+) or below (-) where the donor's lowest joint does. The
    # root delta is a pure translation of the whole solved tree, so adding
    # g to its z (+Z is DOWN: a negative gap RAISES the body) moves every
    # foot point by exactly g and nothing else. `mean` adds ONE number, the
    # clip's mean gap: the feet keep the donor's clearance on average.
    # `frame` adds each frame's own gap: the lowest foot point then sits at
    # the donor's clearance on EVERY frame -- what a crouched bind needs,
    # because straightening a bent leg lengthens it by a different amount
    # on every frame (`GROUNDS` has the table), and `smooth` spreads that
    # over a short window without ever lifting less than the frame needs.
    ground_off = None
    ground_info = None
    if ground:
        solved_pts = None
        gaps, before = [], []
        for k in range(n_keys):
            world, rotations, d = frame_pose(k)
            m16s, _kinds = solve_translations(rotations, tgt.parents, tgt.joints,
                                              {T.pelvis: (P1, d)}, bone_count)
            if solved_pts is None:
                solved_pts = [(p, b) for p, b in foot_pts if b in m16s]
                if not solved_pts:
                    raise RetargetError("--ground: no foot point rides a solved bone "
                                        "(foot bones %s)" % (list(T.foot_bones),))
            low = max(xf_row(_f32(m16s[b]), p)[2] for p, b in solved_pts)
            h = tgt.floor_z - low
            before.append(h)
            gaps.append(h - donor_hover_at(world))
        mean_gap = sum(gaps) / len(gaps)
        ground_off = ([mean_gap] * n_keys if ground == "mean"
                      else ground_smooth(gaps) if ground == "smooth" else list(gaps))
        hop = None
        if hop_cap is not None:
            capped = ground_cap(gaps, ground_off, hop_cap)
            hop = {"cap": float(hop_cap),
                   "framesClamped": sum(1 for a_, b_ in zip(ground_off, capped) if b_ != a_),
                   "excessMaxBefore": round(max(g - o for g, o in zip(gaps, ground_off)), 4),
                   "excessMaxAfter": round(max(g - o for g, o in zip(gaps, capped)), 4)}
            ground_off = capped
        steps = [abs(ground_off[i + 1] - ground_off[i]) for i in range(n_keys - 1)]
        ground_info = {
            "mode": ground,
            # what was ADDED to the root's z per frame (+Z down): hover rises
            # by -offset
            "offset": {"min": round(min(ground_off), 4),
                       "mean": round(sum(ground_off) / n_keys, 4),
                       "max": round(max(ground_off), 4)},
            "meanGap": round(mean_gap, 4),
            "maxStepPerFrame": round(max(steps), 4) if steps else 0.0,
            "hoverBefore": {"min": round(min(before), 3),
                            "mean": round(sum(before) / len(before), 3),
                            "max": round(max(before), 3)},
            "footPoints": len(solved_pts),
            # `--hop-cap`: None when not asked for; else the cap, the frames
            # it raised, and the hover excess over the donor's before/after
            "hopCap": hop,
            "note": "root z += hover_c3 - hover_donor (%s%s); +Z is down"
                    % ("the clip's mean" if ground == "mean" else
                       "per frame" if ground == "frame" else
                       "the lower envelope over +-%d frames, averaged over the "
                       "same window" % GROUND_SMOOTH_HALF,
                       "" if hop is None else
                       ", each frame's excess over the donor's clearance capped at %g"
                       % float(hop_cap)),
        }

    def followed(b, h, k, host_m):
        """Bone `b`'s SOURCE matrix relative to host `h` at frame `k`,
        re-applied to the host's NEW matrix: ``(S_b . S_h^-1) . M_h``."""
        try:
            rel = c3anim.mat_mul4(bonerig._as4(carried(b, k)),
                                  c3anim.mat_inv4(bonerig._as4(carried(h, k))))
        except (c3anim.AnimEditError, ZeroDivisionError):
            raise RetargetError("follow %d:%d: the host's source matrix is singular "
                                "at frame %d" % (b, h, k)) from None
        return _flat16(c3anim.mat_mul4(rel, bonerig._as4(host_m)))

    for k in range(n_keys):
        if k * fps_ms / 1000.0 <= donor.duration:
            frames_in_donor += 1
        world, rotations, d = frame_pose(k)
        if ground_off is not None:
            d = (d[0], d[1], d[2] + ground_off[k])
        root_deltas.append(d)
        m16s, kinds = solve_translations(rotations, tgt.parents, tgt.joints,
                                         {T.pelvis: (P1, d)}, bone_count,
                                         warnings=warn if k == 0 else None)
        mats = []
        for b in range(bone_count):
            m = m16s.get(b)
            if m is None:
                m = carried(b, k)
                kinds[b] = "carried"
            else:
                # ROUND TO FLOAT32 HERE, not at serialisation: every number
                # below (connectivity, the residual, the socket check, the
                # seams) is then measured on the floats the engine reads,
                # and `parse_moti(serialize_moti(m))` hands them back exact.
                m = _f32(m)
            mats.append(m)
        # followers: after every solved bone has its rounded matrix
        for fb, fh in follow_map.items():
            if fb in m16s or fh not in m16s:
                continue
            mats[fb] = _f32(followed(fb, fh, k, mats[fh]))
            kinds[fb] = "follow"
        if k == 0:
            kinds_seen = dict(kinds)
        # connectivity: every rig edge with a joint
        for b, p in tgt.parents.items():
            if p is None or b not in m16s or p not in m16s:
                continue
            J = tgt.joints.get(joint_key(p, b))
            if J is None:
                continue
            r = joint_residual(mats[p], mats[b], J)
            if r > max_resid:
                max_resid, max_resid_at = r, (k, b)
        # conventionResidual: c3 posed bone direction vs the donor's, same t.
        # Under `direction` this is an identity (0.0 whatever the axis map);
        # it checks the storage convention, and the anatomy block decides.
        for name, b in mapping.items():
            dc = dir_row(mats[b], c3_rest_dir[b])
            dg = axis_convert(donor_dir(name, b, world), A)
            a = angle_between_deg(dc, dg)
            f = resid[b]
            f[0] += a
            f[1] = max(f[1], a)
            f[2] += 1
        # MOTION: the posed left-hand bone's centroid x relative to the posed
        # pelvis pivot, against the donor LeftHand's x relative to Hips, mapped.
        if hand_c is not None and left_bone in m16s and T.pelvis in m16s:
            hx = xf_row(mats[left_bone], hand_c)[0] - xf_row(mats[T.pelvis], P1)[0]
            dx = axis_convert(v_sub(donor.position("LeftHand", world),
                                    donor.position("Hips", world)), A)[0]
            hand_agree += int((hx > 0) == (dx > 0))
        # hover: +Z is down, so the lowest posed point is the largest z; the
        # donor's is its lowest SKELETON joint (`Donor.hover_joints`, never a
        # static scene node at the origin) above its rest floor, in c3 units
        if foot_pts:
            low = max(xf_row(mats[b], p)[2] for p, b in foot_pts
                      if b < bone_count)
            hover.append(tgt.floor_z - low)
            donor_hover.append(donor_hover_at(world))
        if drawn_pts:
            low = max(xf_row(mats[b], p)[2] for p, b in drawn_pts if b < bone_count)
            drawn_hover.append(drawn_floor - low)
        keys.append((k if resample else int(src.keys[k].frame), mats))
        if progress and k % 100 == 0:
            progress(k, n_keys)

    # -- the body track's encoding and length -----------------------------
    # Unchanged count: the source encoding, same keys, same byte length --
    # the 2026-09-30 assertion (RAW carries one 64-byte matrix per bone per
    # frame, so same counts = same length). `--frames`: a dense track at the
    # new count, in the source's encoding where `encode_body` round-trips it
    # bit-exact (KKEY, XKEY), else RAW (ZKEY); both lengths recorded. RAW ->
    # RAW at a new count is the one arm the engine was PROVED to honour
    # (Guard 900, 2026-10-01); every other encoding at a new count is an
    # unverified engine acceptance, flagged below.
    src_bytes = len(chunks[body_slot][1])
    want_enc = "RAW" if str(encoding).lower() == "raw" else src.encoding
    if not resample:
        motion = effects.Motion(bone_count, frame_count, src.encoding,
                                [effects.MotionKey(f, m) for f, m in keys],
                                src.extra_channels, 0, 0, src.extra_payload,
                                src.trailing)
        enc_written, enc_note = src.encoding, "%s (source %s)" % (src.encoding, src.encoding)
        body_bytes = effects.serialize_moti(motion)
        if len(body_bytes) != src_bytes:
            raise RetargetError("new body track is %d bytes, the original %d"
                                % (len(body_bytes), src_bytes))
    else:
        # the extraChannels payload is `extra * frameCount * 4` bytes long;
        # at a new frameCount it cannot be carried as it was
        extra, payload = src.extra_channels, src.extra_payload
        if extra and out_frame_count != frame_count:
            warn.append("body track carried %d extra channel(s) (%d bytes) the "
                        "engine seeks past; dropped at the new frame count"
                        % (extra, len(payload)))
            extra, payload = 0, b""
        motion, enc_written, enc_note = encode_body(keys, bone_count, out_frame_count,
                                                    want_enc, extra, payload, src.trailing)
        body_bytes = effects.serialize_moti(motion)
    engine_verified = (not resample) or (src.encoding == "RAW" and enc_written == "RAW")
    if resample and not engine_verified:
        warn.append("body written %s at %d frames from a %s source: the engine was "
                    "proved to honour a new frameCount on RAW -> RAW only (Guard "
                    "900, 2026-10-01); this encoding at this count is unverified"
                    % (enc_written, out_frame_count, src.encoding))
    new_bodies = {body_slot: body_bytes}

    # -- sockets ------------------------------------------------------------
    s_carried: list = []
    if not resample:
        s_bodies, s_rows, s_warns = c3rig.regenerate_sockets(chunks, slots, rig,
                                                             motion, body_ordinal=body_ord)
    else:
        s_bodies, s_rows, s_carried, s_warns = regenerate_sockets_resampled(
            chunks, slots, rig, motion, body_slot)
    new_bodies.update(s_bodies)
    warn.extend(s_warns)
    # check: socket == constant . host on a few keys
    for row in s_rows:
        slot = slots[row["ordinal"]]
        const = rig.socket_constants[str(row["ordinal"])]["constant"]
        c4 = bonerig._as4(const)
        sock = effects.parse_moti(new_bodies[slot])
        worst = 0.0
        for k in (0, row["keys"] // 2, row["keys"] - 1):
            h4 = bonerig._as4(motion.keys[k].matrices[row["host"]])
            want = c3anim.mat_mul4(c4, h4)
            got = bonerig._as4(sock.keys[k].matrices[0])
            worst = max(worst, max(abs(want[r][c] - got[r][c])
                                   for r in range(4) for c in range(4)))
        row["maxElementError"] = worst
        row["encoding"] = sock.encoding
        row["bytes"] = len(new_bodies[slot])
        row["bytesBefore"] = len(chunks[slot][1])
    # sockets the rig knows nothing about (no constant at all) when the
    # count is unchanged: carried byte-for-byte, and said
    if not resample:
        known = {int(k) for k in (rig.socket_constants or {})}
        for ordinal, slot in enumerate(slots):
            if slot == body_slot or ordinal in known:
                continue
            try:
                sk = effects.parse_moti(chunks[slot][1])
                s_carried.append({"ordinal": ordinal, "encoding": sk.encoding,
                                  "keys": len(sk.keys), "frameCount": sk.frame_count,
                                  "why": "no socket constant on the rig"})
            except Exception as e:                           # noqa: BLE001
                s_carried.append({"ordinal": ordinal, "why": "unreadable: %s" % e})

    # Every non-body chunk (PHY, CAME, the sockets left alone) is carried
    # BYTE-FOR-BYTE: `build_c3` re-emits the original (tag, body) pairs.
    out = [(t, new_bodies.get(i, b)) for i, (t, b) in enumerate(chunks)]
    res.bytes = c3write.build_c3(out)
    res.motion = motion

    counts = {"mapped": 0, "rigid": 0, "carried": 0}
    for b in range(bone_count):
        kind = kinds_seen.get(b, "carried")
        counts[kind] = counts.get(kind, 0) + 1

    # THE SEAMS. Two discontinuities a viewer can see: where the tail begins
    # (the last frame inside the donor against the first frame past it --
    # under `loop` the donor's restart, under `hold` its last step; the pair
    # the client shows, 301 -> 302 here, NOT frame 301 against frame 0) and
    # where the client wraps frame_count-1 to 0 (`Phy_NextFrame` is modulo).
    # Reported as the largest bone-direction jump so the choice of tail is a
    # number.
    def _dir_jump(ma, mb):
        return max(angle_between_deg(dir_row(ma[b], c3_rest_dir[b]),
                                     dir_row(mb[b], c3_rest_dir[b]))
                   for b in mapping.values())
    seams = {"clipWrapDeg": round(_dir_jump(keys[-1][1], keys[0][1]), 2)}
    if frames_in_donor < n_keys and frames_in_donor >= 1:
        seams["donorRestartDeg"] = round(_dir_jump(keys[frames_in_donor - 1][1],
                                                   keys[frames_in_donor][1]), 2)
        seams["donorRestartAtFrame"] = frames_in_donor
    residual = {}
    for b, (s, mx, n) in sorted(resid.items()):
        residual[str(b)] = {"name": offsets[b]["name"],
                            "meanDeg": round(s / n, 3) if n else None,
                            "maxDeg": round(mx, 3), "frames": n}

    def _stats(xs):
        return ({"min": round(min(xs), 3), "mean": round(sum(xs) / len(xs), 3),
                 "max": round(max(xs), 3), "frames": len(xs)} if xs else None)

    res.manifest = {
        "fpsMs": fps_ms,
        "framesWritten": n_keys,
        "frameCount": out_frame_count,
        # `--frames`: the source's count, the written count, and whether the
        # engine was PROVED to honour this (RAW -> RAW only, Guard 900)
        "frames": {"source": frame_count, "sourceKeys": n_src,
                   "written": out_frame_count, "resampled": bool(resample),
                   "carriedBonesResampled": sorted(carried_src),
                   "engineVerified": bool(engine_verified)},
        "encoding": {"source": src.encoding, "written": enc_written, "note": enc_note,
                     "changed": enc_written != src.encoding,
                     "engineVerified": bool(engine_verified)},
        "bodyOrdinal": {"container": body_ord, "rig": int(rig.body_ordinal),
                        "how": "the MOTI with the most bones in this container"
                               if body_ordinal is None else "given"},
        "tables": T.describe(),
        "pelvis": T.pelvis,
        "rerooted": tgt.rerooted,
        "framesWithinDonor": frames_in_donor,
        "donorDurationS": donor.duration,
        "targetDurationS": n_keys * fps_ms / 1000.0,
        "tail": tail,
        "rootMotion": {"mode": root_motion, "scale": scale,
                       "c3PelvisHeight": pelvis_h, "donorHipsHeight": hips_h,
                       "pelvisPivot": list(P1),
                       "maxDelta": [max(abs(d[i]) for d in root_deltas)
                                    for i in range(3)],
                       # per frame, so a reader can assert the pelvis pivot
                       # landed at P1 + delta in the file (the root form of
                       # the pivot bug is a translation of the model origin)
                       "deltas": [[round(x, 5) for x in d] for d in root_deltas]},
        "restOffset": {"mode": rest_offset,
                       "worst": {"bone": worst_b, "name": offsets[worst_b]["name"],
                                 "deg": round(offsets[worst_b]["deg"], 3)},
                       "perBone": {str(b): {"name": o["name"],
                                            "deg": round(o["deg"], 3),
                                            "donorRestDirC3": o["donorRestDirC3"],
                                            "c3RestDir": o["c3RestDir"],
                                            "how": o["how"]}
                                   for b, o in sorted(offsets.items())}},
        "directionCentroidSource": {str(b): s for b, s
                                    in sorted(tgt.centroid_source.items())},
        # how every mapped bone's rest direction was read (`Target.direction_how`)
        "directionHow": {str(b): tgt.direction_how.get(b) for b in sorted(offsets)},
        # per mapped foot: the proxy kept, or the sole rule with its numbers
        # (`foot_rest_direction`, `FOOT_PROXY_PITCH_TOL_DEG`)
        "footRest": {str(b): r for b, r in sorted(foot_rest.items())},
        "garment": {"name": getattr(garment, "name", ""),
                    "vertices": len(garment.vertices),
                    "skinnedBones": sorted(tgt.garment_centroids)} if garment else None,
        # the mesh the client draws when it is neither body above (the
        # player route): what `hover.drawn` was measured on
        "drawn": {"name": getattr(drawn, "name", ""),
                  "vertices": len(drawn.vertices),
                  "skinnedBones": sorted(bonetree.bone_centroids(drawn.vertices)),
                  "floorZ": round(float(drawn_floor), 4)} if drawn_pts else None,
        "mapping": {n: b for n, b in sorted(mapping.items(), key=lambda kv: kv[1])},
        "fingers": bool(fingers),
        "attach": {str(b): p for b, p in sorted(tgt.attach.items())},
        # bones carried RELATIVE to a host (`derive_followers`), and the
        # `--ground` term (None: not asked for)
        "followers": {str(b): h for b, h in sorted(follow_map.items())},
        "ground": ground_info,
        # per applied bone: 'default for shape 2' / 'user' / 'derived: ...'
        "attachSource": {str(b): attach_src[b] for b in sorted(tgt.attach)},
        "attachRefused": {str(b): r for b, r
                          in sorted(tgt.attach_refused.items())},
        # socket hosts the rig gives no place: which were outside, what the
        # clips derived for the uncovered ones, and any left carried (each
        # of those is a WARNING above)
        "socketHosts": socket_hosts,
        "axisMap": {"c3_from_gltf": [list(r) for r in A],
                    "det": round(m3_det(A), 6),
                    "shipped": A == AXIS_MAP,
                    "note": axis_note(A)},
        # The checks the rest-pose probe cannot do (module docstring, THE
        # PROBE IS DEGENERATE): det -1, FRONT, SIDE (keyed on the v_l_weapon
        # socket's host) and MOTION. Which alternative each one rejects is
        # the decision table in the docstring; the mirror CONTROL in
        # `tests/test_c3retarget.py` runs them.
        "anatomy": {
            "front": {"donorFootToToeBaseVsC3FootToToeDeg": front,
                      "limitDeg": 45.0,
                      "ok": bool(front) and max(front.values()) < 45.0},
            "side": side,
            "motion": {"bone": left_bone, "donorJoint": "LeftHand",
                       "xSignAgreement": round(hand_agree / n_keys, 4),
                       "framesAgreeing": hand_agree, "frames": n_keys,
                       "cut": MOTION_CUT,
                       "ok": hand_agree > MOTION_CUT * n_keys,
                       "note": "posed bone-%s centroid x - posed pelvis pivot x, "
                               "vs mapped donor (LeftHand - Hips) x" % left_bone},
        },
        # Lowest posed vertex of the feet/toes (reference mesh + garment,
        # dominant bone in FOOT_BONES) above the reference sole, per frame;
        # positive = above the floor. `donor` is the donor's lowest skeleton
        # joint (`donorJoints` of them, `Donor.hover_joints`) above its rest
        # floor, in c3 units, the same frames.
        "hover": {"c3": _stats(hover), "donor": _stats(donor_hover),
                  "bones": list(T.foot_bones), "points": len(foot_pts),
                  "donorJoints": len(donor.hover_joints),
                  # the DRAWN mesh's own feet above its own sole (None when
                  # the drawn mesh is the reference body or the garment)
                  "drawn": (dict(_stats(drawn_hover), points=len(drawn_pts))
                            if drawn_hover else None)},
        "counts": counts,
        "seams": seams,
        "kinds": {str(b): kinds_seen.get(b, "carried") for b in range(bone_count)},
        "connectivity": {"maxResidual": max_resid,
                         "at": {"frame": max_resid_at[0], "bone": max_resid_at[1]}
                         if max_resid_at else None,
                         "edgesChecked": sum(1 for b, p in tgt.parents.items()
                                             if p is not None and
                                             tgt.joints.get(joint_key(p, b)))},
        "conventionResidual": residual,
        "conventionResidualMeanOverBones": round(
            sum(v["meanDeg"] for v in residual.values()) / len(residual), 3)
        if residual else None,
        "sockets": s_rows,
        "socketsCarried": s_carried,
        "body": {"slot": body_slot, "encoding": enc_written,
                 "encodingBefore": src.encoding,
                 "boneCount": bone_count, "keys": n_keys,
                 "bytesBefore": len(chunks[body_slot][1]),
                 "bytesAfter": len(body_bytes)},
        "referenceMesh": {"name": getattr(mesh, "name", ""),
                          "vertices": len(mesh.vertices),
                          "skinnedBones": sorted(tgt.centroids),
                          "floorZ": tgt.floor_z},
    }
    return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_attach(s: str) -> dict:
    out = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        b, _, p = part.partition(":")
        if not _:
            raise RetargetError("--attach wants bone:parent, got %r" % part)
        out[int(b)] = int(p)
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Retarget a glTF (Mixamo) clip onto a C3 body track.")
    ap.add_argument("--gltf", required=True, help="scene.gltf or .glb")
    ap.add_argument("--animation", type=int, default=0)
    ap.add_argument("--root", default=None)
    ap.add_argument("--shape", default="3")
    ap.add_argument("--target", default="c3/0003/000/003.c3")
    ap.add_argument("--rig", default=None, help="a saved rig json")
    ap.add_argument("--max-frames", type=int, default=bonerig.DEFAULT_MAX_FRAMES)
    ap.add_argument("--fps-ms", type=float, default=DEFAULT_FPS_MS,
                    help="frame interval, ms (41 inferred; 33 is the rival)")
    ap.add_argument("--tail", choices=TAILS, default="loop",
                    help="frames past the donor's end: loop it or hold the "
                         "last pose")
    ap.add_argument("--root-motion", choices=ROOT_MOTIONS, default="vertical")
    ap.add_argument("--rest-offset", choices=REST_OFFSETS, default="direction")
    ap.add_argument("--fingers", action="store_true",
                    help="map the first thumb/index phalanges too. OFF by "
                         "default: MEASURED rest offsets of 64.7 / 66.5 deg on "
                         "the two thumbs (12 / 20; their joints come from the "
                         "few clips where fingers articulate at all) and the "
                         "v_r_weapon socket (ordinal 2) rides bone 20, so a "
                         "mis-posed thumb swings a weapon")
    ap.add_argument("--attach", default=None,
                    help="bone:parent pairs made rigid with a parent the rig "
                         "lacks (a root counts); a bone the rig parents is "
                         "refused with a warning; '' for none. Default: the "
                         "shape's table (`default_attach`: the toes 45:44,50:49 "
                         "everywhere, plus the hat host 7:6 on shape 2); a "
                         "socket host the rig leaves carried is derived from "
                         "the family's clips and attached when it equals a rig "
                         "bone, and is a WARNING otherwise")
    ap.add_argument("--garment", default=None,
                    help="the garment whose v_body supplies direction "
                         "centroids the reference mesh lacks (the toes, so "
                         "the feet point where the donor's do); an asset "
                         "path under the install, '' for none. Default: the "
                         "shape's own c3/mesh/00N188495.c3 when the install "
                         "has it (`default_garment`), else none")
    ap.add_argument("--out", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    # -- monster / NPC targets (tools/rigtarget.py), 2026-10-01 -------------
    ap.add_argument("--npc", default=None,
                    help="an NPC family instead of --shape: geometry id "
                         "(9992720), npc.json type (3) or name (Pharmacist). "
                         "--shape takes a 3-digit MONSTER shape too, resolved "
                         "through ini/3dmotion.ini to its c3/monster/<dir>/")
    ap.add_argument("--map", choices=("p84", "auto"), default="p84",
                    help="p84: the hand-written shape-3 tables. auto: every "
                         "table DERIVED from core/bonelabel labels of the "
                         "loaded rig (`derive_tables`; on the p84 rig the two "
                         "are equal, byte for byte in the output), behind the "
                         "humanoid gate: a target missing a core label or "
                         "carrying a duplicate is REFUSED by name. --attach "
                         "defaults to the tables' (45:44,50:49 on p84; the "
                         "garment-only toes auto finds; none on a monster)")
    ap.add_argument("--frames", type=int, default=None,
                    help="write N keys at --fps-ms whatever the source holds "
                         "(302 = one donor loop at 41 ms; the engine honours "
                         "frameCount, proved on Guard 900 2026-10-01). Default: "
                         "the source's count")
    ap.add_argument("--encoding", choices=("keep", "raw"), default="keep",
                    help="with --frames: keep the source encoding where it "
                         "round-trips bit-exact (KKEY, XKEY; ZKEY falls to RAW "
                         "and is flagged), or write RAW")
    ap.add_argument("--mesh", default=None,
                    help="the reference (drawn) mesh, an asset path; default: "
                         "the family's drawn mesh for a monster/NPC target, "
                         "else the clip's own v_body")
    ap.add_argument("--ground", nargs="?", const="smooth", default=None,
                    choices=GROUNDS,
                    help="add a vertical root term so the feet keep the donor's "
                         "clearance: 'smooth' (bare --ground: never below it, "
                         "no hip pop), 'frame' (exactly it, every frame) or "
                         "'mean' (one number for the clip). OFF by default: "
                         "every player-route build keeps its bytes. For a body "
                         "whose bind is crouched (monster 129 sank 6-23 units)")
    ap.add_argument("--hop-cap", type=float, default=None,
                    help="with --ground: the most a frame's hover may exceed the "
                         "donor's clearance, in c3 units (ground_cap); the frames "
                         "over it take a root step of the difference instead of a "
                         "hop. OFF by default (None); the NPC builder passes %g "
                         "(GROUND_HOP_CAP has the measurements)" % GROUND_HOP_CAP)
    ap.add_argument("--borrow", choices=("auto", "off"), default="auto",
                    help="--npc only. auto: an NPC whose standby holds the "
                         "player's bone count on the player's own skeleton "
                         "(`rigtarget.player_body`: Sage) is built through the "
                         "PLAYER route with that shape's rig; off: its own "
                         "family rig, always")
    return ap


def main(argv=None) -> int:
    """`_main`, with a refusal printed as ONE line and exit 1 -- never a
    traceback: a non-humanoid monster, an unknown shape, a target this tool
    cannot honestly retarget are findings, not crashes."""
    try:
        return _main(argv)
    except (RetargetError, rigtarget.TargetError) as e:
        print("REFUSED: %s" % e)
        return 1


def _main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    root = str(a.root or coroot.default_root())
    ar = coassets.AssetRoot.bare(root)
    t0 = time.time()
    # -- the target: a player shape, a 3-digit monster shape or --npc --------
    # (tools/rigtarget.py). A monster or NPC family's rig lives in its own
    # cache file; it is solved and SAVED here on a miss and handed down as
    # `--rig`, so the player-shape block below runs unchanged.
    target = rigtarget.resolve(root, shape=a.shape, npc=a.npc, assets=ar)
    target_rig_how = None
    borrowed = None
    if target.kind != rigtarget.PLAYER:
        if a.target == ap.get_default("target"):
            if not target.idle:
                raise RetargetError("%s has no idle clip to retarget: %s"
                                    % (target.family, "; ".join(target.notes)))
            a.target = target.idle[0]
        # A PLAYER-BODIED NPC (Sage) borrows the player's rig and runs the
        # player route: the player's tables, the player's reference body,
        # its own mesh as the garment, and a host for every bone it skins
        # that the player rig does not place.
        if target.kind == rigtarget.NPC and not a.rig and a.borrow != "off":
            borrowed = borrowed_route(ar, target, root, max_frames=a.max_frames)
        if borrowed is not None:
            if borrowed["unresolved"]:
                raise RetargetError(
                    "%s %s: %s, but skinned bone(s) %s of %s are outside the player "
                    "rig and no host could be derived for them"
                    % (target.kind, target.id, borrowed["why"], borrowed["unresolved"],
                       target.drawn_mesh))
            a.rig = borrowed["rigFile"]
            target_rig_how = borrowed["why"]
            if a.map == "auto":
                print("  note: --map auto does not apply: %s" % borrowed["why"])
                a.map = "p84"
            if not a.quiet:
                print("  " + borrowed["why"])
                print("  followers: %s" % json.dumps(
                    {b: r["host"] for b, r in borrowed["followers"].items()}))
        if not a.rig:
            fam_warn: list = []
            _rig, rig_file, target_rig_how = rigtarget.load_or_solve(
                target, root, max_frames=a.max_frames, assets=ar,
                mesh_path=a.mesh, warnings=fam_warn)
            a.rig = str(rig_file)
            for w in fam_warn:
                print("  " + w)
        if a.garment is None or a.garment == DEFAULT_GARMENT:
            # the PLAYER's garment is not this body's: its centroids are
            # keyed by p84 bone index and would pose a monster's bones.
            # (None is the per-shape default since 2026-10-01, and
            # `default_garment(a.shape)` would hand an `--npc` target the
            # player's, a.shape being '3' there.)
            a.garment = ""
        if a.map != "auto" and borrowed is None:
            print("  note: %s %s with --map p84 applies shape 3's bone tables to "
                  "another skeleton; --map auto derives them from its labels"
                  % (target.kind, target.id))

    class _A:                                   # what c3rig._load_or_solve reads
        rig = a.rig
        shape = a.shape
        max_frames = a.max_frames
    space = bonerig.space_for_shape(a.shape)
    # The cache is keyed per SHAPE (2026-10-01; `bonerig.rig_path`): before,
    # shapes 2/3/4 shared one `p84.json`, `_load_or_solve` re-solved 41-59 s
    # whenever the file held another shape, and nothing saved that solve.
    # `find_rig` also reads the pre-keyed name when it holds THIS shape, and
    # that file is then migrated to its per-shape name here.
    keyed_rig = bonerig.rig_path(space, root, a.shape)
    found_rig, legacy = bonerig.find_rig(space, a.shape, root)
    # A loaded rig with no census (the pre-#200 pooled solver's) is named
    # here and reported with the run's warnings: on the CCO snapshot that
    # solver keys bone 65 as a root, so the flap is carried.
    rig_warn: list = []
    rig = c3rig._load_or_solve(_A, root, warnings=rig_warn)
    rig_loaded_from = (a.rig if a.rig
                       else str(found_rig) if found_rig is not None
                       else None)
    rig_note = None
    rig_saved_to = None
    if not a.rig and found_rig is None:
        # `_load_or_solve` never saves; without this a box with no rig file
        # for this shape re-solves 22-59 s on every run. Saved where
        # `c3rig solve` would, under the per-shape name.
        rig_saved_to = str(bonerig.save(rig, root=root))
    elif not a.rig and legacy:
        # The space-only file held this shape: copy it to the keyed name so
        # the next run (and `c3rig`) find it there. The old file is left in
        # place -- nothing else is known to read it, and deleting a cache
        # another checkout's tool might still open is not this run's call.
        rig_saved_to = str(bonerig.save(rig, keyed_rig))
        rig_note = ("loaded from the pre-2026-10-01 space-only cache %s and "
                    "migrated to %s" % (found_rig, keyed_rig))
    if not a.quiet:
        print("rig %s: %d bones, body ordinal %d, %d joints, %d sockets (%.1fs)%s"
              % (rig.space, rig.bone_count, rig.body_ordinal,
                 len(rig.joint_points), len(rig.socket_constants or {}),
                 time.time() - t0,
                 (", " + rig_note) if rig_note else
                 (", saved to %s" % rig_saved_to) if rig_saved_to else
                 (", loaded from %s" % rig_loaded_from)))

    # -- the reference mesh, the tables and the humanoid gate ---------------
    data = ar.read(a.target)
    if borrowed is not None and not a.mesh:
        mesh, mesh_how = borrowed["mesh"], borrowed["meshHow"]
    else:
        mesh, mesh_how = reference_mesh_for(ar, target, data, a.mesh)
    if mesh is None:
        raise RetargetError(mesh_how)
    # The garment, resolved ONCE and before the tables: per shape (A4,
    # 2026-10-01 -- shape 3's mesh was every shape's default and gave shape
    # 4 the wrong body's thumb centroid, motion 0.536 FAIL), and `--map
    # auto` reads the SAME one for its toes, or the derived tables of the
    # player's own rig would lose 45/50 and stop equalling `P84_TABLES`.
    if a.garment is None:
        garment_path, garment_how = default_garment(a.shape, ar)
    else:
        garment_path, garment_how = a.garment, ("--garment" if a.garment
                                                else "--garment '' (none)")
    tables = None
    verdict = None
    if a.map == "auto":
        garment_for_tables = None
        if garment_path:
            try:
                garment_for_tables = garment_mesh(ar.read(garment_path))
            except Exception:                                 # noqa: BLE001
                garment_for_tables = None
        tables, verdict, _labels = derive_tables(rig, garment=garment_for_tables,
                                                 mesh=mesh)
        if not a.quiet:
            d = tables.describe()
            print("map auto: %s" % verdict["reason"])
            print("  labels %s" % json.dumps(d["labels"], sort_keys=True))
            print("  mapping %s" % json.dumps(d["mapping"]))
            print("  directionChild %s  hipChildren %s  frameAxis %s  attach %s  "
                  "footBones %s  fingers %s  parentLine %s  pelvis %s"
                  % (d["directionChild"], d["hipChildren"], d["frameAxis"],
                     d["attach"], d["footBones"], d["fingers"],
                     d["directionInherit"], d["pelvis"]))
            for n in tables.notes:
                print("  note: " + n)
        if not verdict["ok"]:
            raise RetargetError("%s %s: %s" % (target.kind, target.id, verdict["reason"]))
    if not a.quiet:
        print("reference mesh: %s (%d vertices, %d skinned bones)"
              % (mesh_how, len(mesh.vertices), len(bonetree.bone_centroids(mesh.vertices))))

    gltf_path = Path(a.gltf)
    gltf_bytes = gltf_path.read_bytes()
    g = gltfread.Gltf.load(gltf_path)
    donor = Donor(g, a.animation, mapping=tables.mapping if tables else None)
    if not a.quiet:
        print(g.summary())

    data = ar.read(a.target)
    sha_before = _sha(data)
    garment = None
    garment_warn = []
    if borrowed is not None and not garment_path:
        garment, garment_how = borrowed["garment"], borrowed["garmentHow"]
        garment_path = target.drawn_mesh
    elif garment_path:
        try:
            garment = garment_mesh(ar.read(garment_path))
        except Exception as e:                                # noqa: BLE001
            garment_warn.append("--garment %s unreadable (%s); direction "
                                "centroids come from the reference mesh only"
                                % (garment_path, e))
        else:
            if garment is None:
                garment_warn.append("--garment %s has no skinned v_body; "
                                    "direction centroids come from the "
                                    "reference mesh only" % garment_path)

    def prog(k, n):
        if not a.quiet:
            print("  frame %d/%d..." % (k, n), flush=True)

    # Per shape (2026-10-02): None is `default_attach(shape)` -- shape 2's
    # adds the hat host 7:6 -- and `assets` lets a host the table misses be
    # derived from the clips; an explicit --attach is the user's set.
    attach = None if a.attach is None else _parse_attach(a.attach)
    attach_source = None if a.attach is None else "user (--attach %r)" % a.attach
    t1 = time.time()
    res = retarget_clip(data, rig, donor, fps_ms=a.fps_ms, tail=a.tail,
                        root_motion=a.root_motion, rest_offset=a.rest_offset,
                        attach=attach, attach_source=attach_source, assets=ar,
                        fingers=bool(a.fingers),
                        garment=garment, progress=prog, mesh=mesh,
                        tables=tables, frames=a.frames, encoding=a.encoding,
                        ground=a.ground, hop_cap=a.hop_cap,
                        follow=borrowed["follow"] if borrowed is not None else None,
                        drawn=borrowed.get("drawn") if borrowed is not None else None)
    res.warnings[:0] = rig_warn
    res.warnings.extend(garment_warn)
    elapsed = time.time() - t1

    # a monster/NPC run is named by its FAMILY: `--npc` leaves a.shape at
    # '3', and `--shape c3/monster/125` is a path
    run = a.name or ("retarget-%s-%s" % (
        "shape%s" % a.shape if target.kind == rigtarget.PLAYER else target.family,
        time.strftime("%Y%m%d-%H%M%S")))
    out_dir = Path(a.out) if a.out else (Path(coroot.export_dir()) / "rig" / run)
    manifest = {
        "tool": "c3retarget",
        "commandLine": " ".join(["py -3 tools/c3retarget.py"] + list(argv if argv is not None else sys.argv[1:])),
        "root": root,
        "shape": str(a.shape),
        "targetFamily": dict(target.as_dict(), rigHow=target_rig_how),
        "borrowed": ({"shape": borrowed["shape"], "why": borrowed["why"],
                      "test": borrowed["test"], "followers": borrowed["followers"]}
                     if borrowed is not None else None),
        "map": a.map,
        "humanoid": verdict,
        "referenceMeshHow": mesh_how,
        "donor": dict(donor.describe(), path=str(gltf_path), sha256=_sha(gltf_bytes)),
        "target": {"path": a.target, "shaBefore": sha_before,
                   "shaAfter": _sha(res.bytes), "bytesBefore": len(data),
                   "bytesAfter": len(res.bytes)},
        "rig": {"space": rig.space, "boneCount": rig.bone_count,
                "bodyOrdinal": rig.body_ordinal, "solvedFrom": rig.solved_from,
                "shape": rig.shape,
                "loadedFrom": rig_loaded_from,
                "savedTo": rig_saved_to,
                "note": rig_note,
                "parents": {str(k): v for k, v in sorted(rig.parents.items())}},
        "garmentPath": garment_path or None,
        "garmentSource": garment_how,
        "elapsedS": round(elapsed, 2),
        "dryRun": bool(a.dry_run),
        "warnings": res.warnings,
        "revert": ("delete this folder / comod uninstall; nothing was written "
                   "to the game install"),
    }
    manifest.update(res.manifest)

    if not a.dry_run:
        dst = out_dir / a.target
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(res.bytes)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=1, sort_keys=True), "utf-8")

    m = res.manifest
    print("%s %s: %d keys %s, %d -> %d bytes, %.1fs"
          % ("would write" if a.dry_run else "wrote", a.target,
             m["framesWritten"], m["body"]["encoding"], len(data),
             len(res.bytes), elapsed))
    print("  donor %.3f s, %d of %d frames inside it, tail %s, %.0f ms/frame"
          % (m["donorDurationS"], m["framesWithinDonor"], m["framesWritten"],
             m["tail"], m["fpsMs"]))
    print("  frames %d -> %d (%s), encoding %s, body ordinal %d in the container "
          "(rig %d), engine verified %s; tables: %s"
          % (m["frames"]["source"], m["frames"]["written"],
             "resampled" if m["frames"]["resampled"] else "same count",
             m["encoding"]["note"], m["bodyOrdinal"]["container"],
             m["bodyOrdinal"]["rig"], m["frames"]["engineVerified"],
             m["tables"]["source"]))
    if m["socketsCarried"]:
        print("  sockets carried byte-for-byte: %s" % m["socketsCarried"])
    print("  counts: %s   seams (max bone-direction jump): %s"
          % (m["counts"], m["seams"]))
    print("  attach: %s; socket hosts outside the rig: %s%s"
          % (", ".join("%s:%s (%s)" % (b, p, m["attachSource"][b])
                       for b, p in m["attach"].items()) or "none",
             m["socketHosts"]["outsideRig"] or "none",
             (" -- UNATTACHED: %s" % m["socketHosts"]["unattached"])
             if m["socketHosts"]["unattached"] else ""))
    print("  root motion %s, scale %.3f (c3 %.1f / donor %.3f), max |delta| %s"
          % (m["rootMotion"]["mode"], m["rootMotion"]["scale"],
             m["rootMotion"]["c3PelvisHeight"], m["rootMotion"]["donorHipsHeight"],
             ["%.2f" % v for v in m["rootMotion"]["maxDelta"]]))
    print("  connectivity max residual %.3e over %d edges (frame %s bone %s)"
          % (m["connectivity"]["maxResidual"], m["connectivity"]["edgesChecked"],
             (m["connectivity"]["at"] or {}).get("frame"),
             (m["connectivity"]["at"] or {}).get("bone")))
    print("  rest offset %s; conventionResidual (deg, c3 posed dir vs donor "
          "dir; an identity under `direction`):" % m["restOffset"]["mode"])
    for b, f in m["conventionResidual"].items():
        off = m["restOffset"]["perBone"][b]["deg"]
        print("    %2s %-16s offset %6.2f   mean %6.2f   max %6.2f"
              % (b, f["name"], off, f["meanDeg"], f["maxDeg"]))
    w = m["restOffset"]["worst"]
    print("  worst rest offset: bone %d (%s) %.2f deg; conventionResidual mean "
          "over bones %.2f" % (w["bone"], w["name"], w["deg"],
                               m["conventionResidualMeanOverBones"]))
    an = m["anatomy"]
    print("  axis map %s" % m["axisMap"]["note"])
    print("  anatomy: front %s (< %.0f) %s; side: donor LeftHand x %+.3f vs c3 "
          "bone %s (%s) x %+.1f %s, LeftHand mapped to it %s; "
          "motion bone-%s x-sign agreement %.3f %s"
          % (an["front"]["donorFootToToeBaseVsC3FootToToeDeg"],
             an["front"]["limitDeg"], "OK" if an["front"]["ok"] else "FAIL",
             an["side"]["donorLeftHandRestXc3"], an["side"]["leftHandBone"],
             "mapped LeftHand; side CARRIED, not keyed" if an["side"].get("carried")
             else "v_l_weapon host",
             an["side"]["c3LeftHandCentroidX"] or 0.0,
             "OK" if an["side"]["agree"] else "FAIL",
             "OK" if an["side"]["mappingOk"] else "FAIL",
             an["motion"]["bone"], an["motion"]["xSignAgreement"],
             "OK" if an["motion"]["ok"] else "FAIL"))
    hv = m["hover"]
    if hv["c3"]:
        print("  hover (lowest foot/toe point above the sole, %d points): c3 "
              "min %.2f mean %.2f max %.2f; donor min %.2f mean %.2f max %.2f"
              % (hv["points"], hv["c3"]["min"], hv["c3"]["mean"], hv["c3"]["max"],
                 hv["donor"]["min"], hv["donor"]["mean"], hv["donor"]["max"]))
    gr = m.get("ground")
    if gr:
        print("  ground (%s): root z offset min %.2f mean %.2f max %.2f (+Z down), "
              "largest step %.2f/frame; hover before min %.2f mean %.2f max %.2f"
              % (gr["mode"], gr["offset"]["min"], gr["offset"]["mean"],
                 gr["offset"]["max"], gr["maxStepPerFrame"], gr["hoverBefore"]["min"],
                 gr["hoverBefore"]["mean"], gr["hoverBefore"]["max"]))
    if m.get("followers"):
        print("  followers (source motion kept relative to a host): %s"
              % json.dumps(m["followers"]))
    for s in m["sockets"]:
        print("  socket %d -> host %d, %d keys, %s, max element error %.2e"
              % (s["ordinal"], s["host"], s["keys"], s["encoding"],
                 s["maxElementError"]))
    if res.warnings:
        print("  %d warning(s):" % len(res.warnings))
        for w in res.warnings:
            print("    " + w)
    if not a.dry_run:
        print("  -> %s" % out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
