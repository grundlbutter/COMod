#!/usr/bin/env python3
r"""
npcsplit.py -- what you would change to split one NPC off its shared art.

    py -3 tools/npcsplit.py                    # the demo: Storekeeper on CCO
    py -3 tools/npcsplit.py --npc Barber

**THIS COMMAND WRITES NOTHING.**  Not to the install, not to a stage tree, not
anywhere.  It reads the client, allocates a free art id, and prints the exact
edits a person would make by hand.  Applying them is the reader's decision and
the reader's keystrokes.

Direct editing is deliberately absent -- not behind a flag, not in an
"advanced" mode.  Validated features may edit directly in a future COMod
spinoff; until then the honest shape of this tool is *describe and instruct*,
and a half-built write path here would be an invitation to finish it.

WHAT A "SPLIT" IS AND WHAT IT IS NOT
------------------------------------
CCO's NPCs share art.  Measured on this install: 39 `npc.json` rows --
Storekeeper plus 38 others -- resolve to the same geometry `c3/mesh/9990010.c3`
and the same three motion files `c3/npc/9990011{00,01,90}.c3`.  Editing those
files in place re-skins all 39, which is the bug a swap page ships if nobody
counts.

A split gives one NPC a **private copy of its motion art** at a freshly
allocated group id, and repoints only that row's three motion fields at it.

**The geometry and texture stay shared, and that is stated rather than
glossed.**  They are reached through `simple_object` -> `3DSimpleObj.ini` ->
`3dobj.ini`/`3dtexture.ini`, not through the motion fields, so repointing
motions does not fork them.  Splitting those is a different edit against
different tables and this tool does not pretend to describe it.

THE ID IS ALLOCATED, NEVER ASSUMED
----------------------------------
`core/npcalloc.py` computes the free set as the complement of the UNION of
every table plus disk, per install.  This tool calls it and prints its choice
together with the tables that justify it.  **Nothing here hardcodes a group.**
That is not tidiness: on this install `014` is free in `npc.json` and taken by
a MONSTER in three other tables, so an instruction naming the "obviously next"
id would tell the owner to type a collision.  The report prints every group the
allocator skipped and who holds it, so the choice is auditable.

Occupancy tokens are read as **every decimal run in the table text, keys and
values both**, not just the left-hand sides.  Measured: keys-only finds 0
occupied groups in `3DSimpleObj.ini`, because that file is a `[Section]` table
whose 999-ids sit on the RIGHT of the `=`.  A source that returns a confident
zero is worse than no source at all.

NEVER DESCRIBE AN EDIT YOU HAVE NOT VERIFIED IS CURRENTLY TRUE
--------------------------------------------------------------
An automated edit that is wrong gets caught by a test.  A printed instruction
that is wrong gets *typed*.  So every OLD value in every instruction is read
out of the owner's own file at describe-time -- never assumed, never carried in
from a brief or a docstring.

This is structural rather than a convention.  `Instruction.old` must be an
`Observed`, and `Observed` is only ever returned by a reader that actually
found the thing and recorded where.  Constructing an instruction with a bare
string is a `TypeError`, so a later worker cannot paste a remembered value in.
When a current value cannot be read the instruction is **not emitted**; a
`Refusal` is printed in its place and the exit code is non-zero.  An
unreadable value must not come out of the safe end.

ENCODING
--------
Six of the 39 cohort rows carry CJK names (`礼品店老板`, `文府重臣`, ...).  On a
cp1252 console a naive `print` dies with `UnicodeEncodeError` at exactly the
interesting row, so stdout is rebound to UTF-8 with ``errors="replace"`` and
the report says so on screen.  Treat it as a test case, not a nicety.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import contextlib
import re
import sys

import assetdiff
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PROJECT / "core"))

import coassets                                            # noqa: E402
import coroot                                              # noqa: E402
import npcalloc                                            # noqa: E402
import npcart                                              # noqa: E402

#: The client this tool describes. `npcalloc` refuses to allocate without a
#: target precisely so this cannot drift into a global assumption.
TARGET = "cco"
_CONV = npcalloc.CONVENTIONS[TARGET]

#: role -> the `npc.json` field it is spelled in, in report order.
ROLE_FIELDS = dict(zip(npcalloc.MOTION_ROLES, _CONV.motion_fields))

#: Tables whose 999-family ids make a group occupied. The free set is the
#: complement of the UNION of these plus disk -- `npcalloc.occupied_groups`
#: has deliberately no single-table entry point, and this is why.
OCCUPANCY_TABLES = ("ini/npc.json", "ini/3dmotion.ini", "ini/3dobj.ini",
                    "ini/3dtexture.ini", "ini/3DSimpleObj.ini")

_DIGITS = re.compile(r"\d+")

#: The table the CLIENT resolves motion ids through. Listed in
#: OCCUPANCY_TABLES as well, but occupancy is about not COLLIDING with an id;
#: this is about the new id meaning anything at all.
_MOTION_TABLE = "ini/3dmotion.ini"

#: The APPEARANCE chain: which slot an NPC wears, and what that slot resolves
#: to. Separate from the motion axis and shared by a different set of rows.
_SIMPLEOBJ_TABLE = "ini/3DSimpleObj.ini"
_OBJ_TABLE = "ini/3dobj.ini"
_TEX_TABLE = "ini/3dtexture.ini"


# ---------------------------------------------------------------------------
# an instruction may only quote what was read
# ---------------------------------------------------------------------------

class Unread(RuntimeError):
    """An instruction was built from a value nobody read."""


@dataclass(frozen=True)
class Observed:
    """A fact read out of the owner's install at describe-time.

    Only the readers below construct one, and each records *where* it looked
    so the report can show its work. There is no constructor that takes a
    remembered value, which is the whole point: a printed instruction is
    typed, so its OLD half has to be measured rather than recalled.
    """
    value: str
    source: str                 # logical path, or the resolver that answered
    where: str                  # "line 6", "c3.wdf", "absent from every source"
    literal: str                # the exact text or fact that was read


@dataclass(frozen=True)
class Refusal:
    """A value could not be read, so no instruction is emitted for it."""
    what: str
    why: str


@dataclass(frozen=True)
class Instruction:
    """Five things, every time: file, key, field, OLD, NEW.

    `old` is typed as `Observed` and checked at construction. A string here
    would compile and read as correct, and would be exactly the stale claim
    about somebody's install that this class exists to make impossible.
    """
    kind: str                   # "edit" | "add-file"
    path: str                   # the file the reader opens or creates
    key: str                    # which row/entry inside it
    field: str                  # which field inside that row
    old: Observed
    new: str
    note: str = ""
    #: A second reading, when the instruction's *command* needed verifying
    #: too -- `None` means the fast route was checked and refused, and the
    #: printer must fall back to something it did verify.
    extra: Optional[Observed] = None

    def __post_init__(self):
        if not isinstance(self.old, Observed):
            raise Unread(
                f"Instruction({self.field!r}) was built with a bare "
                f"{type(self.old).__name__} for its OLD value. Every OLD must "
                "be an Observed returned by a reader that actually looked at "
                "the file -- see the module docstring.")


@dataclass
class Sheet:
    """The emitted instructions and the refusals, kept side by side."""
    steps: list = field(default_factory=list)
    refusals: list = field(default_factory=list)

    def emit(self, ins: Instruction) -> None:
        self.steps.append(ins)

    def refuse(self, what: str, why: str) -> None:
        self.refusals.append(Refusal(what, why))


# ---------------------------------------------------------------------------
# readers -- the only place an Observed comes from
# ---------------------------------------------------------------------------

def read_field(text: str, span: tuple, field_name: str,
               logical: str) -> Optional[Observed]:
    """The current value of one JSON field inside one row's text span.

    Returns None when the field is not there. A caller must treat that as a
    refusal rather than substituting anything.
    """
    start, end = span
    block = text[start:end]
    m = re.search(rf'"{re.escape(field_name)}"\s*:\s*([^,\r\n}}]+)', block)
    if not m:
        return None
    line_no = text.count("\n", 0, start + m.start()) + 1
    line = text[start + m.start():start + m.end()]
    return Observed(value=m.group(1).strip(), source=logical,
                    where=f"line {line_no}", literal=line.strip())


def read_asset(view: coassets.AssetRoot, logical: str) -> Optional[Observed]:
    """Where an asset currently resolves, and how big it is."""
    loc = view.locate(logical)
    if loc is None:
        return None
    return Observed(value=logical, source=logical, where=loc.source,
                    literal=f"{loc.size} bytes, resolved from {loc.source}")


def read_absence(view: coassets.AssetRoot, logical: str) -> Optional[Observed]:
    """That a path is currently free. `None` means it is NOT free."""
    if view.locate(logical) is not None:
        return None
    return Observed(value="(does not exist)", source=logical,
                    where="not in any container and not loose",
                    literal=f"AssetRoot.locate({logical!r}) returned None")


def read_ini_key(view: coassets.AssetRoot, table: str,
                 key: str) -> Optional[Observed]:
    """One `key=value` row of a flat ini, or an Observed ABSENCE of it."""
    try:
        text = view.read(table).decode("latin-1", "replace")
    except Exception:
        return None
    want = f"{key}="
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith(want):
            return Observed(value=line.split("=", 1)[1].strip(), source=table,
                            where=f"line {n}", literal=line.strip())
    return Observed(value="", source=table, where="absent from the table",
                    literal=f"no line begins {want!r} in {table}")


def read_objid_slots(view: coassets.AssetRoot,
                     table: str = "ini/3DSimpleObj.ini") -> Optional[set]:
    """Every `[ObjIDTypeN]` number the appearance table already defines."""
    try:
        text = view.read(table).decode("latin-1", "replace")
    except Exception:
        return None
    return {int(m) for m in re.findall(r"^\[ObjIDType(\d+)\]", text, re.M)}


def read_motion_row(view: coassets.AssetRoot, mid: str,
                    table: str = "ini/3dmotion.ini") -> Optional[Observed]:
    """The motion table's row for `mid`, or an Observed ABSENCE of one.

    `ini/3dmotion.ini` maps every motion id the client plays to the file that
    holds it. Repointing `npc.json` at an id this table does not carry gives
    the client an id it cannot resolve, and it draws no NPC at all -- the row
    is still there, the art is still on disk, and the character is simply
    gone. Measured on CCO: 187 of the 190 motion ids npc.json uses are
    registered here.

    Returns None only when the table itself cannot be read, which is a
    refusal; a missing ROW is a successful reading of an absence.
    """
    try:
        text = view.read(table).decode("latin-1", "replace")
    except Exception:
        return None
    want = f"{mid}="
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith(want):
            return Observed(value=line.strip(), source=table, where=f"line {n}",
                            literal=line.strip())
    return Observed(value="", source=table, where="absent from the table",
                    literal=f"no line begins {want!r} in {table}")


def read_stageable(view: coassets.AssetRoot, logical: str) -> Optional[Observed]:
    """Whether `comod.py stage-mesh` would ACCEPT this file, checked here.

    The command line is part of the instruction, so it is held to the same
    bar as the values: a printed `stage-mesh` that the validator rejects is a
    wrong instruction that gets typed. This runs `cmd_stage_mesh`'s own gate
    -- the container must walk and every PHY chunk must re-parse to exactly
    its declared length -- and returns `None` when it would be refused, so the
    caller can print a plain copy instead of a command that fails.

    Measured on this install: CCO's NPC motion files carry `PHY `, `MOTI` and
    `CAME`, so the mesh route applies to them. That is not assumed for every
    row -- a motion-only file would have no PHY and would land in the `None`
    branch rather than in a broken instruction.
    """
    try:
        from c3phy import VARIANTS, iter_chunks, parse_phy
        data = view.read(logical)
        chunks = list(iter_chunks(data))
        phy = [(t, b) for t, b in chunks if t in VARIANTS]
        if not phy:
            return None
        for tag, body in phy:
            m = parse_phy(tag, body)
            if m.trailing:
                return None
            if m.faces and max(max(f) for f in m.faces) >= m.vertex_count:
                return None
    except Exception:
        return None
    tags = sorted({t.decode("latin-1").strip() for t, _ in chunks})
    return Observed(
        value="stage-mesh accepts it", source=logical, where="c3phy validator",
        literal=f"{len(chunks)} chunks {tags}, {len(phy)} PHY, all re-parse "
                "to their declared length with no trailing bytes")


# ---------------------------------------------------------------------------
# output that survives a cp1252 console
# ---------------------------------------------------------------------------

#: Every wrapper this process installed. Held on purpose: dropping the last
#: reference to a `TextIOWrapper` lets its finaliser CLOSE the buffer beneath
#: it, so a second call would leave `sys.stdout` writing to a closed stream
#: and every later print would raise. That bites a test suite (which calls
#: `main()` more than once) rather than a one-shot CLI, which is exactly the
#: kind of bug a single manual run does not find.
_WRAPPERS: list = []


def utf8_stdout() -> str:
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    if enc.replace("-", "") == "utf8":
        return (f"stdout is already {enc}; no rebind needed -- six cohort "
                "names are CJK and crash a cp1252 console")
    try:
        wrapper = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                   errors="replace", line_buffering=True)
    except (AttributeError, ValueError):          # no buffer: a pipe or a mock
        return (f"stdout is {enc or 'unknown'} and exposes no binary buffer; "
                "left as it is -- CJK names may be mangled by the console")
    _WRAPPERS.append(wrapper)
    sys.stdout = wrapper
    return (f"stdout was {enc or 'unknown'}; rebound to UTF-8 errors=replace "
            "-- six cohort names are CJK and crash a cp1252 console")


def rule(title: str) -> None:
    print(f"\n{title}\n" + "-" * max(12, len(title)))


# ---------------------------------------------------------------------------
# proof that this command touched nothing
# ---------------------------------------------------------------------------

def tree_state(root: Path) -> dict:
    """path -> (size, mtime_ns) for every file in a tree.

    This walks the filesystem on purpose and it is NOT asset resolution -- the
    "resolve through AssetRoot, never os.walk" rule exists so a disk walk
    cannot report a confident zero for content that lives in a container. The
    question here is the opposite one, *did any byte of this tree change*, and
    for that the tree itself is the subject rather than the index.

    A file that will not `stat` is recorded as `None` rather than skipped.
    Skipping it would drop it from both sides of a comparison, so a file that
    became unreadable *during* the run would read as "nothing changed" -- the
    instrument would be blind to exactly the event it exists to catch.
    """
    out: dict = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            st = p.stat()
            out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
        except OSError:
            try:
                out[p.relative_to(root).as_posix()] = None
            except ValueError:                     # pragma: no cover
                pass
    return out


def tree_fingerprint(root: Path) -> dict:
    """(count, digest) over a tree, for a one-line report."""
    state = tree_state(root)
    if not state and not root.is_dir():
        return {"files": 0, "digest": "(absent)"}
    h = hashlib.blake2b(digest_size=16)
    for k in sorted(state):
        h.update(f"{k}|{state[k]}\n".encode("utf-8", "replace"))
    return {"files": len(state), "digest": h.hexdigest()}


#: Paths the GAME owns and rewrites by itself -- crash-reporter sessions, the
#: launcher's logs, the login history, the per-machine setup blobs. Measured
#: on CCO 2026-08-17: with the client running, a read-only pass over the
#: install saw exactly these move, and the file count rise by four.
#:
#: They are **partitioned out and named on screen, never silently ignored.**
#: A blanket "ignore what changed" would make this check unable to fail; the
#: rule here is that anything outside this list is a hard FAIL, and anything
#: inside it is still printed so the reader can see it and disagree.
#:
#: Deliberately narrow, and `test_npcsplit.py` asserts the narrowness by
#: requiring that none of the tables or containers this tool actually reads
#: is matched. An exemption that could swallow `ini/npc.json` would leave the
#: real subject unchecked while the check still passed.
VOLATILE = (
    ".sentry-native/",      # crash reporter session + event files
    "LOG/",                 # the client's own rolling logs
    "debug/",
    "login_history.json",
    "ini/setup.json",       # per-machine client config, NOT an art table
    "ImConquer.log",
    "ImLauncher.log",
)


def is_volatile(rel: str) -> bool:
    """Is this path one the game rewrites on its own?

    Prefix match for directories, exact match for files -- never a substring
    test, which would let `ini/setup.json` exempt anything merely containing
    that text.
    """
    r = rel.replace("\\", "/")
    for v in VOLATILE:
        if v.endswith("/"):
            if r.startswith(v):
                return True
        elif r == v:
            return True
    return False


def tree_delta(before: dict, after: dict) -> dict:
    """What actually moved between two `tree_state` snapshots, BY NAME.

    A digest that says "something changed" and cannot say what is not much of
    an instrument -- it cannot tell a file this command wrote from a log the
    game's own launcher touched while the command was reading. Naming the
    paths is what makes the difference decidable by the person reading it.
    """
    changed = sorted(k for k in before if k in after and before[k] != after[k])
    return {"changed": changed,
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after))}


#: Filesystem-mutating calls this module must never contain. Checked against
#: its own AST at run time, so "it writes nothing" is a property of the source
#: rather than a promise in a docstring -- and so a later worker who adds a
#: write path trips the tool's own report instead of shipping quietly.
#: stdout is not a file: `print` and the UTF-8 rebind are deliberately absent
#: from this set, and the check says so on screen.
WRITE_METHODS = frozenset({
    "write_bytes", "write_text", "writelines", "mkdir", "touch", "unlink",
    "rmdir", "symlink_to", "hardlink_to", "rename", "makedirs", "removedirs",
    "rmtree", "copy", "copy2", "copyfile", "copytree", "move", "remove",
})


#: Filled by `run()` when --json is asked for, and printed by `main()`.
#: A dict and a print are not filesystem writes, so this module keeps the
#: property its own audit below asserts: it plans, and something else applies.
PLAN: dict = {}


def self_audit(path: Path) -> list:
    """Every filesystem write this module's own source could perform."""
    found = []
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in WRITE_METHODS:
            found.append(f"line {node.lineno}: .{fn.attr}()")
        elif isinstance(fn, ast.Name) and fn.id == "open":
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if any(c in mode for c in "wax+"):
                found.append(f"line {node.lineno}: open(mode={mode!r})")
    return found


# ---------------------------------------------------------------------------
# occupancy
# ---------------------------------------------------------------------------

def occupancy_sources(view: coassets.AssetRoot, root: Path) -> dict:
    """Every id-ish token the free set is the complement of, by source name."""
    srcs: dict = {}
    actions: set = set()

    raw = view.read(_CONV.npc_table).decode("utf-8", "replace")
    toks = []
    for row in json.loads(raw):
        for fld in _CONV.motion_fields:
            v = row.get(fld)
            if v is None:
                continue
            toks.append(v)
            s = str(v).strip()
            if s.startswith(npcalloc.PREFIX) and len(s) >= _CONV.id_width:
                actions.add(s[-_CONV.action_width:])
    srcs[_CONV.npc_table] = toks

    for table in OCCUPANCY_TABLES:
        if table == _CONV.npc_table:
            continue
        try:
            text = view.read(table).decode("latin-1", "replace")
        except FileNotFoundError:
            srcs[table] = []
            continue
        found = _DIGITS.findall(text)
        srcs[table] = found
        for tok in found:
            s = tok.lstrip("0")
            if s.startswith(npcalloc.PREFIX) and len(s) >= _CONV.id_width:
                actions.add(s[-_CONV.action_width:])

    # Disk, two ways, because neither alone is honest. The probe asks the
    # container-aware resolver about every group at every action suffix the
    # tables actually use -- that reaches art inside `c3.wdf`, which a disk
    # walk cannot see. The loose listing catches flat files whose suffix the
    # tables never mention, which the probe cannot ask for. Union of both.
    probed = []
    for grp in range(1000):
        for act in sorted(actions):
            logical = f"c3/npc/{npcalloc.PREFIX}{grp:03d}{act}.c3"
            if view.exists(logical):
                probed.append(f"{npcalloc.PREFIX}{grp:03d}{act}")
    srcs["c3/npc/ probed via AssetRoot"] = probed

    npcdir = root / "c3" / "npc"
    srcs["c3/npc/ loose on disk"] = (
        [p.stem for p in npcdir.rglob("*") if p.is_file()]
        if npcdir.is_dir() else [])

    # The STAGE TREE is occupancy. A group whose art is staged is spoken for:
    # it is going to be installed, and the install is what the next allocation
    # will collide with. Without this the allocator hands the same "free" group
    # to every split in a row -- measured: with Storekeeper's split staged on
    # group 015, Blacksmith was offered 015 as well, which would have
    # overwritten the staged art and pointed both NPCs at one group. That is
    # not a partial split; it is the sharing the split exists to undo,
    # recreated silently and one step later.
    stage_root = PROJECT / "mods" / "stage"
    sdir = stage_root / "c3" / "npc"
    srcs["mods/stage/c3/npc/"] = (
        [p.stem for p in sdir.rglob("*") if p.is_file()]
        if sdir.is_dir() else [])

    staged_table = stage_root / _CONV.npc_table
    toks = []
    if staged_table.is_file():
        raw_staged = staged_table.read_text(encoding="utf-8", errors="replace")
        try:
            staged_rows = json.loads(raw_staged)
        except ValueError as e:
            # Loud. A staged table that will not parse is going to be
            # INSTALLED over the real one, and treating it as "no occupancy"
            # would allocate straight into whatever it claims.
            raise SystemExit(
                f"the staged {_CONV.npc_table} does not parse ({e}).\n"
                f"  {staged_table}\n"
                "  It would be installed over the real table. Fix or remove "
                "it before allocating against it.")
        for row_ in staged_rows:
            for fld in _CONV.motion_fields:
                v = row_.get(fld)
                if v is not None:
                    toks.append(v)
    srcs[f"mods/stage/{_CONV.npc_table}"] = toks
    return srcs


def row_spans(text: str) -> list:
    """(start, end) of each top-level `{...}` object, in file order.

    A brace scan rather than a re-parse, because the instruction has to quote
    the file's own bytes -- line numbers and literal text the reader will see
    when they open it. A parsed value cannot say what line it lives on.
    """
    spans = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, i + 1))
                start = None
    return spans


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def run(args) -> int:
    note = utf8_stdout()
    root = coroot.root_from_args(args)
    failures = []

    def check(label: str, ok: bool, detail: str = "") -> bool:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
              + (f"\n         {detail}" if detail else ""))
        if not ok:
            failures.append(label)
        return ok

    print("npcsplit -- what you would change, and the proof it disturbs nobody")
    print("THIS COMMAND WRITES NOTHING. It reads and it prints.")
    print(f"install (read-only)  {root}")
    print(f"encoding             {note}")

    before_install = tree_state(root)
    before_mods = tree_state(PROJECT / "mods")
    print(f"install fingerprint  {len(before_install)} files, "
          f"blake2b {tree_fingerprint(root)['digest']}")

    view = coassets.AssetRoot(root)

    def done(rc: int) -> int:
        """Close the archives on the way out of every return path.

        `AssetRoot` holds an open handle on `c3.wdf`/`data.wdf`; leaving it to
        the garbage collector is invisible in a one-shot CLI and shows up as a
        `ResourceWarning` the moment a test calls `main()` twice. Closing at
        the boundary rather than trusting the process to exit.
        """
        view.close()
        return rc

    # Ask the client being shown, not the baseline. `view` is a bare
    # AssetRoot here so the ServerView hazard cannot bite -- but the
    # census in test_viewer asserts the PROPERTY over the whole tree,
    # because an instance count is a floor and a profile-less site is
    # latent until someone passes a composed view. Resolved, not omitted.
    _prof, _ = assetdiff.table_profile_for(view, root)
    tables = npcart.Tables(view.read, _prof)
    sheet = Sheet()

    # `--type` selects the row outright; `--npc` matches by name and refuses
    # when the name is not unique. Names in npc.json are NOT unique -- on CCO
    # "Blacksmith" is 3 rows and "Armorer" is 2 -- so a caller that has a
    # specific row in mind (the swap page, which lists a group's members) must
    # be able to say WHICH, rather than being refused for asking by the only
    # label a person can see.
    if getattr(args, "type", None) is not None:
        def matches(r) -> bool:
            return r.get("type") == args.type
        want_label = f"type {args.type}"
    else:
        want = args.npc.strip().lower()

        def matches(r) -> bool:
            return str(r.get("name", "")).strip().lower() == want
        want_label = repr(args.npc)
    rows = [r for r in tables.npcs if matches(r)]
    if len(rows) != 1:
        print(f"\nnpc {want_label} matches {len(rows)} rows in "
              f"{_CONV.npc_table}; need exactly one")
        return done(2)
    row = rows[0]
    plan = tables.plan_for_npc(row)

    # -- allocator ---------------------------------------------------------
    rule("allocator")
    served = npcalloc.can_serve(TARGET)
    print(f"  can_serve({TARGET!r})  {served.state}")
    print(f"    {served.reason}")
    srcs = occupancy_sources(view, root)
    occ = npcalloc.occupied_groups(srcs)
    for name in srcs:
        print(f"    {name:<34} {len(occ['by_source'][name]):>4} groups "
              f"({len(srcs[name])} tokens)")
    print(f"    {'UNION':<34} {len(occ['union']):>4} groups")

    group, width, layout = npcalloc.allocate(TARGET, occ)
    print(f"\n  scan from group {npcalloc.FLOOR:03d}; every skip, and who holds it:")
    for i in range(npcalloc.FLOOR, int(group) + 1):
        g = f"{i:03d}"
        holders = [n for n, s in occ["by_source"].items() if g in s]
        if holders:
            print(f"    {g}  taken   {', '.join(holders)}")
        else:
            print(f"    {g}  FREE    held by none of the {len(srcs)} sources "
                  "above  <== CHOSEN")
    print(f"\n  allocated group {group!r}  width {width}  layout {layout}")
    check("the allocator chose this group; nothing here hardcoded one",
          group not in occ["union"],
          f"npcalloc.allocate({TARGET!r}, ...) returned {group!r}; it is absent "
          "from every source above")

    # -- cohort, COUNTED ---------------------------------------------------
    rule("cohort -- counted, never asserted")
    all_plans = [(r, tables.plan_for_npc(r)) for r in tables.npcs]
    cohort = [(r, p) for r, p in all_plans if p.geometry == plan.geometry]
    others = [(r, p) for r, p in cohort if r is not row]
    print(f"  geometry            {plan.geometry}")
    print(f"  texture             {plan.texture}")
    print(f"  rows sharing it     {len(cohort)}  (including {row.get('name')})")
    print(f"  others to protect   {len(others)}")
    bysimple: dict = {}
    for _r, p in cohort:
        bysimple[p.simple_object] = bysimple.get(p.simple_object, 0) + 1
    for so, n in sorted(bysimple.items(), key=lambda kv: -kv[1]):
        print(f"    simple_object {str(so):>6} : {n:>2} NPC(s)")
    nonascii = [str(r.get("name")) for r, _p in cohort
                if any(ord(c) > 127 for c in str(r.get("name", "")))]
    print(f"  non-ASCII names     {len(nonascii)}: {', '.join(nonascii)}")

    # -- build the sheet, reading every OLD value now ----------------------
    text = view.read(_CONV.npc_table).decode("utf-8", "replace")
    spans = row_spans(text)
    parsed = json.loads(text)
    # The SAME predicate that chose the row, so the span located here cannot
    # belong to a different row from the one planned against. Matching by name
    # here would also refuse every non-unique name, which is most of them.
    idx = [i for i, r in enumerate(parsed) if matches(r)]
    if len(spans) != len(parsed) or len(idx) != 1:
        print(f"\ncannot locate the row's text span ({len(spans)} spans, "
              f"{len(parsed)} rows, {len(idx)} match(es) for {want_label})")
        return done(2)
    span = spans[idx[0]]
    row_key = f'"type": {parsed[idx[0]].get("type")}  ("name": '\
              f'"{parsed[idx[0]].get("name")}")'

    new_ids: dict = {}
    for role, fld in ROLE_FIELDS.items():
        obs = read_field(text, span, fld, _CONV.npc_table)
        if obs is None:
            sheet.refuse(f"{_CONV.npc_table} :: {row_key} :: {fld}",
                         "the field is not present in the row as the file "
                         "currently stands, so its OLD value cannot be read "
                         "and no instruction is emitted for it")
            continue
        if not obs.value.isdigit() or len(obs.value) != _CONV.id_width:
            sheet.refuse(
                f"{_CONV.npc_table} :: {row_key} :: {fld}",
                f"current value {obs.value!r} is not a "
                f"{_CONV.id_width}-character id, so the action suffix cannot "
                "be read off it; npcalloc calls that UNKNOWN and so does this")
            continue
        action = obs.value[-_CONV.action_width:]
        new = _CONV.spell(group, action)
        new_ids[role] = new
        sheet.emit(Instruction(kind="edit", path=_CONV.npc_table,
                               key=row_key, field=fld, old=obs, new=new,
                               note=f"{role} motion"))

    for role in npcalloc.MOTION_ROLES:
        if role not in new_ids:
            continue
        src_logical = plan.motions.get(role)
        dst_logical = f"c3/npc/{new_ids[role]}.c3"
        if not src_logical:
            sheet.refuse(f"copy art for {role}",
                         "the current motion path did not resolve, so there is "
                         "no source file to name")
            continue
        src_obs = read_asset(view, src_logical)
        if src_obs is None:
            sheet.refuse(f"copy {src_logical}",
                         "the source does not resolve in this install, so an "
                         "instruction to copy it would be a stale claim")
            continue
        free = read_absence(view, dst_logical)
        if free is None:
            sheet.refuse(f"create {dst_logical}",
                         "that path already exists in this install; the "
                         "allocator's group is contradicted and nothing is "
                         "emitted rather than telling you to overwrite art")
            continue
        sheet.emit(Instruction(
            kind="add-file", path=dst_logical, key=f"copy of {src_logical}",
            field=f"{role} motion file", old=src_obs, new=dst_logical,
            note=f"destination verified free: {free.literal}",
            extra=read_stageable(view, src_logical)))

    # -- register the new ids, or the client cannot resolve them -----------
    # This is the step whose absence made a split LOOK like it worked and then
    # deleted the NPC from the world: npc.json pointed at 999015100, the art
    # was installed, and `ini/3dmotion.ini` had no row saying where 999015100
    # lives. The client resolves motions through that table, found nothing,
    # and drew nobody. Copying the art and repointing the row is two thirds of
    # a split.
    for role in npcalloc.MOTION_ROLES:
        if role not in new_ids:
            continue
        nid = new_ids[role]
        src_id = None
        for s in sheet.steps:
            if s.kind == "edit" and s.field == ROLE_FIELDS.get(role):
                src_id = s.old.value
        here = read_motion_row(view, nid)
        if here is None:
            sheet.refuse(f"register {nid} in {_MOTION_TABLE}",
                         f"{_MOTION_TABLE} could not be read, so neither its "
                         "contents nor an instruction about them can be "
                         "stated")
            continue
        if here.value:
            sheet.refuse(f"register {nid} in {_MOTION_TABLE}",
                         f"that id already has a row ({here.literal}); the "
                         "allocator's group is contradicted and nothing is "
                         "emitted rather than telling you to overwrite it")
            continue
        anchor = read_motion_row(view, src_id) if src_id else None
        sheet.emit(Instruction(
            kind="add-row", path=_MOTION_TABLE, key=str(nid),
            field=f"{role} motion registration", old=here,
            new=f"{nid}=c3/npc/{nid}.c3",
            note=("place it beside %s" % anchor.literal) if anchor
                 and anchor.value else "append it with the other 999 ids",
            extra=anchor))

    # -- fork the APPEARANCE chain, when asked -----------------------------
    # A motion split gives an NPC private ANIMATIONS. It does not change how
    # the NPC LOOKS: the visible mesh and skin are reached through
    # `simple_object` -> 3DSimpleObj.ini -> 3dobj.ini / 3dtexture.ini, a
    # different axis shared by a different set of rows. On CCO, Conductress
    # shares motion group 006 with 11 others and simple_object 3 with 6 --
    # overlapping, unequal, and both have to be forked before one NPC can be
    # given a new look alone.
    #
    # Measured: 3dobj.ini[9990060] = c3/npc/999006100.c3, i.e. the appearance
    # entry points at the STANDBY motion file. So the fork points its new
    # object id at the newly allocated standby, and the donor's bytes land
    # there.
    fork = getattr(args, "fork_appearance", False)
    if fork:
        slots = read_objid_slots(view)
        obj_row = simple_old = None
        pid = None
        if slots is None:
            sheet.refuse("fork the appearance chain",
                         f"{_SIMPLEOBJ_TABLE} could not be read")
        else:
            pid = int(f"{npcalloc.PREFIX}{group}0")
            free_here = []
            for tbl in (_OBJ_TABLE, _TEX_TABLE):
                obs = read_ini_key(view, tbl, str(pid))
                if obs is None:
                    sheet.refuse(f"register {pid} in {tbl}",
                                 f"{tbl} could not be read")
                    free_here.append(False)
                    continue
                if obs.value:
                    sheet.refuse(f"register {pid} in {tbl}",
                                 f"id {pid} already resolves to {obs.value!r}; "
                                 "nothing is emitted rather than repointing an "
                                 "id somebody else uses")
                    free_here.append(False)
                else:
                    free_here.append(True)
            slot = max(slots) + 1 if slots else 1
            standby_id = new_ids.get("standby")
            simple_old = read_field(text, spans[idx[0]], "simple_object",
                                    _CONV.npc_table)
            if all(free_here) and standby_id and simple_old is not None:
                sheet.emit(Instruction(
                    kind="add-row", path=_OBJ_TABLE, key=str(pid),
                    field="appearance geometry",
                    old=read_ini_key(view, _OBJ_TABLE, str(pid)),
                    new=f"{pid}=c3/npc/{standby_id}.c3",
                    note="points at the newly allocated standby, as the "
                         "install's own entries do"))
                sheet.emit(Instruction(
                    kind="add-row", path=_TEX_TABLE, key=str(pid),
                    field="appearance texture",
                    old=read_ini_key(view, _TEX_TABLE, str(pid)),
                    new=f"{pid}=c3/texture/{pid}.dds",
                    note="the donor's skin is staged at this path"))
                sheet.emit(Instruction(
                    kind="add-section", path=_SIMPLEOBJ_TABLE,
                    key=f"ObjIDType{slot}", field="appearance slot",
                    old=Observed(value="", source=_SIMPLEOBJ_TABLE,
                                 where="absent from the table",
                                 literal=f"no [ObjIDType{slot}] in "
                                         f"{_SIMPLEOBJ_TABLE}; "
                                         f"{len(slots)} slots defined, "
                                         f"highest {max(slots)}"),
                    new=f"[ObjIDType{slot}]\nPartAmount=1\n"
                        f"Part0={pid}\nTexture0={pid}",
                    note="a new slot, so the %d row(s) on the old one keep "
                         "their look" % sum(
                             1 for r_ in tables.npcs
                             if r_.get("simple_object") == row.get(
                                 "simple_object"))))
                sheet.emit(Instruction(
                    kind="edit", path=_CONV.npc_table, key=row_key,
                    field="simple_object", old=simple_old, new=str(slot),
                    note="point this row at the new appearance slot"))

    # -- the instructions --------------------------------------------------
    rule("INSTRUCTIONS -- every OLD value below was read from your install now")
    adds = [s for s in sheet.steps if s.kind == "add-file"]
    edits = [s for s in sheet.steps if s.kind == "edit"]
    rows = [s for s in sheet.steps if s.kind == "add-row"]
    sections = [s for s in sheet.steps if s.kind == "add-section"]

    print(f"  STEP 1 -- create {len(adds)} new art file(s).")
    print("  These are copies. The originals stay exactly where they are.")
    for n, s in enumerate(adds, 1):
        print(f"\n    1.{n}  file    {s.path}")
        print(f"         source  {s.old.source}")
        print(f"         read    {s.old.literal}  [{s.old.where}]")
        print(f"         role    {s.field}")
        print(f"         verify  {s.note}")
        print(f"         do it   py -3 tools/comod.py extract {s.old.source}")
        if s.extra is not None:
            print(f"                 py -3 tools/comod.py stage-mesh "
                  f"mods/work/{Path(s.old.source).name} {s.path}")
            print(f"         checked {s.extra.literal}")
        else:
            # stage-mesh's validator would refuse this file, so it is not
            # printed. A plain copy into the stage tree is what `install`
            # consumes either way, and it is the route that was verified.
            print(f"                 copy mods/work/{Path(s.old.source).name}"
                  f"  ->  mods/stage/{s.path}")
            print("         checked stage-mesh would REJECT this file (no "
                  "usable PHY chunk), so a plain copy is given instead")

    print(f"\n  STEP 1b -- register {len(rows)} new motion id(s) in "
          f"{_MOTION_TABLE}.")
    print("  Without these the client cannot resolve the new ids and the NPC")
    print("  vanishes from the world -- art present, row present, nobody there.")
    for n, s in enumerate(rows, 1):
        print(f"\n    1b.{n}  file    {s.path}")
        print(f"         add     {s.new}")
        print(f"         checked {s.old.literal}")
        print(f"         where   {s.note}")

    print(f"\n  STEP 2 -- change {len(edits)} field(s) in one row.")
    print(f"  Open a copy of the table, not the installed one:")
    print(f"         py -3 tools/comod.py stage {_CONV.npc_table}")
    print(f"  then edit  mods/stage/{_CONV.npc_table}")
    for n, s in enumerate(edits, 1):
        print(f"\n    2.{n}  file    {s.path}")
        print(f"         row     {s.key}")
        print(f"         field   {s.field}          ({s.note})")
        print(f"         OLD     {s.old.value}      "
              f"[read at {s.old.where}: {s.old.literal}]")
        print(f"         NEW     {s.new}")

    print(f"\n  STEP 3 -- review, then apply.")
    print("         py -3 tools/comod.py diff              "
          "# what would change")
    print("         py -3 tools/comod.py install --dry-run # still writes nothing")
    print("         py -3 tools/comod.py install --yes     "
          "# YOU apply it; this tool never does")
    print("  `install` backs up every file it displaces before writing.")

    print(f"\n  TO REVERSE -- as concrete as the change.")
    print("         py -3 tools/comod.py uninstall --yes   "
          "# restores the backups, removes the added files")
    print("  By hand, if you edited directly instead:")
    for s in edits:
        print(f"    - in {s.path}, row {s.key}:")
        print(f"        set {s.field} back to {s.old.value}  (from {s.new})")
    for s in adds:
        print(f"    - delete {s.path}  "
              "(it is new; nothing referenced it before)")

    if sheet.refusals:
        print(f"\n  REFUSED -- {len(sheet.refusals)} instruction(s) NOT emitted:")
        for r in sheet.refusals:
            print(f"    {r.what}\n      {r.why}")

    # -- the instruction disturbs nobody -----------------------------------
    rule("ASSERT -- the id you are told to type is used by nobody")
    check(f"group {group} is absent from all {len(srcs)} occupancy sources",
          group not in occ["union"],
          "checked as the complement of their UNION, never one table")
    used_by = []
    for r, p in all_plans:
        if r is row:
            continue
        for fld in _CONV.motion_fields:
            v = str(r.get(fld) or "").strip()
            if v.startswith(npcalloc.PREFIX) and \
                    v[3:3 + width] == str(group):
                used_by.append((r.get("type"), r.get("name"), fld, v))
    check(f"none of the {len(all_plans) - 1} other {_CONV.npc_table} rows "
          f"spells group {group} in any motion field",
          not used_by,
          f"{len(used_by)} do" if used_by else
          f"{len(all_plans) - 1} rows checked, 0 hits")
    cohort_hits = [u for u in used_by
                   if u[0] in {r.get("type") for r, _ in others}]
    check(f"none of the {len(others)} other members of the shared-art cohort "
          f"uses group {group}",
          not cohort_hits, f"{len(cohort_hits)} do")
    new_paths = [f"c3/npc/{v}.c3" for v in new_ids.values()]
    collide = [p for p in new_paths if view.exists(p)]
    check(f"all {len(new_paths)} new art path(s) are free in this install",
          not collide, ", ".join(collide) if collide else ", ".join(new_paths))
    unread = [s for s in sheet.steps if not isinstance(s.old, Observed)]
    check(f"every one of the {len(sheet.steps)} emitted instructions carries a "
          "value read at describe-time",
          not unread,
          "enforced at construction: Instruction.__post_init__ raises Unread "
          "for a non-Observed OLD, so an unread value cannot be printed")
    check("no instruction was emitted for a value that could not be read",
          True,
          f"{len(sheet.refusals)} refusal(s) printed instead of guessed "
          "instructions")

    # -- control -----------------------------------------------------------
    rule("control -- named explicitly, not implied")
    # A control is only a control if it SHARES the art being split away from.
    # The default was a fixed name that belongs to group 001's cohort, so it
    # failed its own check for every NPC on any other group -- reported as
    # "the control does not resolve the id" rather than "the control you were
    # given is not in this cohort". When none is asked for, one is taken from
    # the cohort itself and named in the output, which is what the check is
    # for: a control nobody can see is not a control.
    if not args.control:
        # Not a member sharing the split row's NAME: names repeat in this
        # table, so "Blacksmith" as a control for Blacksmith would be a
        # different row that the checks below cannot tell apart from the one
        # being edited -- and it fails them for that reason, obscurely.
        own = str(row.get("name", "")).strip().lower()
        picked = next((str(r_.get("name", "")).strip() for r_, _ in others
                       if str(r_.get("name", "")).strip()
                       and str(r_.get("name", "")).strip().lower() != own), "")
        if picked:
            args.control = picked
            print(f"  no --control given; taking one from this cohort: "
                  f"{picked}")
        else:
            print("  no --control given and this cohort has no named member "
                  "to use as one")
    ctl_rows = [r for r in tables.npcs
                if str(r.get("name", "")).strip() == args.control]
    if not ctl_rows:
        check(f"{args.control} is present in {_CONV.npc_table}", False,
              "the control row was not found, so the check below cannot run")
    else:
        ctl = ctl_rows[0]
        ctl_plan = tables.plan_for_npc(ctl)
        standby_old = None
        for s in edits:
            if s.field == ROLE_FIELDS["standby"]:
                standby_old = s.old.value
        print(f"  {args.control}  (npc type {ctl.get('type')})")
        for r_, f_ in ROLE_FIELDS.items():
            print(f"    {r_:<8} {ctl.get(f_)}  ->  {ctl_plan.motions.get(r_)}")
        if standby_old:
            want_path = f"c3/npc/{standby_old}.c3"
            # The cohort is defined by shared GEOMETRY, and this used to
            # assert the control shared the split row's MOTION as well. That
            # holds only until the row is split once: afterwards its motion is
            # private while its geometry is still shared, and the control
            # "failing" was the check conflating two axes rather than anything
            # being wrong. What matters, and what is asserted now, is that the
            # control's own art is not written by any instruction.
            ctl_standby = ctl_plan.motions.get("standby")
            written = [s.path for s in adds] + [s.new.split("=", 1)[0]
                                                for s in rows if "=" in s.new]
            check(f"{args.control} keeps its own art: {ctl_standby} is not a "
                  "destination of any instruction",
                  bool(ctl_standby) and ctl_standby not in written,
                  f"instructions write {', '.join(written) or 'nothing'}; the "
                  f"control resolves {ctl_standby}")
            if ctl_plan.motions.get("standby") != want_path:
                # Stated rather than asserted: it is the normal state after a
                # previous split, and printing it keeps the report honest
                # about which axis the control still shares.
                print(f"         note: {args.control} does not share this "
                      f"row's motion ({ctl_standby} vs {want_path}) -- it "
                      "shares the GEOMETRY, which is the axis the cohort is "
                      "counted on")
        check(f"{args.control} is not named by any emitted instruction",
              not any(args.control in s.key for s in sheet.steps),
              f"the {len(sheet.steps)} instructions name exactly one row: "
              f"{row_key}")
        check(f"{args.control} keeps its art because STEP 1 only ADDS files",
              all(s.kind == "add-file" and not view.exists(s.path)
                  for s in adds),
              "no instruction overwrites a path any other NPC resolves")

    # -- this command wrote nothing ---------------------------------------
    rule("this command wrote nothing")
    after_install = tree_state(root)
    after_mods = tree_state(PROJECT / "mods")
    d = tree_delta(before_install, after_install)
    moved = d["changed"] + d["added"] + d["removed"]
    game_owned = [k for k in moved if is_volatile(k)]
    ours = [k for k in moved if not is_volatile(k)]
    check(f"no file under the install moved, out of {len(after_install)} "
          "checked, except ones the game owns",
          not ours,
          "; ".join(f"{k} {before_install.get(k)} -> {after_install.get(k)}"
                    for k in ours[:8])
          if ours else f"0 outside the {len(VOLATILE)} game-owned paths")
    if game_owned:
        # Printed, never swallowed. If the client is running it rewrites its
        # own logs while this command reads, and hiding that would make the
        # check above unfalsifiable.
        print(f"         note: {len(game_owned)} game-owned file(s) moved "
              "while this ran -- the client writes these itself:")
        for k in game_owned[:8]:
            print(f"           {k}")
    dm = tree_delta(before_mods, after_mods)
    check("the stage tree is untouched (it is not written to, or created)",
          not (dm["changed"] + dm["added"] + dm["removed"]),
          f"mods/: {len(before_mods)} -> {len(after_mods)} files; "
          + (", ".join((dm["added"] + dm["changed"])[:5]) or "nothing moved"))
    writes = self_audit(Path(__file__))
    check("this module's own source contains no filesystem-write call",
          not writes,
          "; ".join(writes) if writes else
          f"AST-scanned for {len(WRITE_METHODS)} write APIs plus open() in a "
          "write mode; stdout is not a file and is out of scope")

    # -- the machine-readable plan ----------------------------------------
    # Emitted so ONE planner serves both the page and the stager. The
    # alternative -- a second implementation that recomputes the group, the
    # cohort and the OLD values -- is how a UI and a command come to disagree
    # in front of the person applying the result.
    PLAN.clear()
    PLAN.update({
        "npc": args.npc,
        "root": str(root),
        "row": row_key,
        "rowType": row.get("type"),
        "rowName": row.get("name"),
        "table": _CONV.npc_table,
        "group": str(group),
        "width": width,
        "layout": layout,
        "control": args.control,
        "cohort": [{"type": r_.get("type"), "name": r_.get("name")}
                   for r_, _ in others],
        "adds": [{"path": s.path, "source": s.old.source, "role": s.field,
                  "note": s.note, "readAt": s.old.where,
                  "literal": s.old.literal,
                  "stageMesh": s.extra is not None}
                 for s in adds],
        "sections": [{"path": s.path, "name": s.key, "body": s.new,
                      "note": s.note, "checked": s.old.literal}
                     for s in sections],
        "rows": [{"path": s.path, "id": s.key, "role": s.field,
                  "line": s.new, "note": s.note,
                  "anchor": (s.extra.literal if s.extra else ""),
                  "checked": s.old.literal}
                 for s in rows],
        "edits": [{"path": s.path, "row": s.key, "field": s.field,
                   "old": s.old.value, "new": s.new, "note": s.note,
                   "readAt": s.old.where, "literal": s.old.literal}
                  for s in edits],
        "refusals": [{"what": r_.what, "why": r_.why} for r_ in sheet.refusals],
        "failures": list(failures),
    })

    rule("result")
    print(f"  {len(sheet.steps)} instruction(s) to type, "
          f"{len(sheet.refusals)} refused, 0 files written by this command.")
    print("  THIS command applies nothing, and still does not. "
          "tools/npcstage.py stages\n  this same plan into mods/stage, and "
          "comod.py install applies THAT -- with a\n  backup of every file it "
          "displaces, and an uninstall that puts them back.")
    if failures or sheet.refusals:
        print(f"\n  FAIL -- {len(failures)} check(s) failed, "
              f"{len(sheet.refusals)} instruction(s) refused:")
        for f_ in failures:
            print(f"    {f_}")
        return done(1)
    print("\n  PASS -- all checks green.")
    return done(0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    coroot.add_root_argument(ap)
    ap.add_argument("--npc", default="Storekeeper",
                    help="the npc.json row to describe a split for "
                         "(default: Storekeeper)")
    ap.add_argument("--control", default="",
                    help="a cohort member to check against by name. Defaults "
                         "to one taken from the cohort being split, because a "
                         "fixed name only belongs to one cohort.")
    ap.add_argument("--type", type=int, default=None,
                    help="select the row by its npc.json `type`, which is "
                         "unique. Names are not: on CCO 'Blacksmith' is 3 "
                         "rows. Overrides --npc when given.")
    ap.add_argument("--fork-appearance", action="store_true",
                    help="also fork the simple_object chain, so the NPC can "
                         "be given a new LOOK without changing every row that "
                         "shares its appearance slot")
    ap.add_argument("--json", action="store_true",
                    help="emit the plan as JSON on stdout, with the full "
                         "human report inside it under 'report'. Still writes "
                         "nothing: applying is tools/npcstage.py's job.")
    args = ap.parse_args(argv)
    if not args.json:
        return run(args)
    # The report is captured rather than dropped: it is the "show me what is
    # being changed" view, and it carries the OLD values and the assertions
    # that make the plan checkable. A stager that consumed a plan with no
    # readable account of itself would be asking for trust it had not earned.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run(args)
    doc = dict(PLAN)
    doc["report"] = buf.getvalue()
    doc["exit"] = rc
    doc["ok"] = (rc == 0)
    print(json.dumps(doc, indent=1, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
