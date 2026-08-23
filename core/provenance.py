#!/usr/bin/env python3
r"""
provenance.py -- record which install a derived artefact came from, and refuse
one that came from somewhere else.

WHY THIS IS ONE MODULE AND NOT SIX FIXES
----------------------------------------
Reading this repository's handoffs as a set, the same defect appears six times
wearing six costumes:

  * ``out/`` was not keyed by client, so eight installs shared one tree
  * ``parts._ACTION_CAT`` cached one motion catalogue per *process*, and a
    script walking three clients measured the first one three times
  * ``out/dll/`` holds artefacts from two installs and three dates; its
    ``rtti.md`` is still titled for one client over a body describing another
  * ``out/meshtex/`` was regenerated mid-measurement and the change was
    attributed to the code being measured
  * ``out/thumbs/manifest.json`` records *how* it rendered and never *what
    from*
  * the viewer suite gates on ``HAVE_ROOT`` -- "does a directory exist" -- so
    it cannot tell a wrong client from broken code

Those are not six bugs. They are one missing invariant, stated here once:

    **Every derived artefact records what it was derived from, and every
    loader refuses one it did not derive.**

Fixing it six times in six styles is how one of them ends up subtly wrong and
stays wrong -- which is the argument `safepath` already made for path
confinement, and it applies here for the same reason.

WHAT MAKES THIS CHECKABLE AT ALL
--------------------------------
`coroot.base_id` already answers "which install is this?" as
``<kind>-<fingerprint>``, where the fingerprint is a content hash of the
``ini/`` table layer.  This module does not invent identity; it writes that
answer down beside the data and compares it on the way back in.

``out/offsets_cache.json`` is the shape being generalised.  It keys on the
sha256 of the module bytes and `load_cache` returns ``None`` on mismatch --
the only loader in the repository that refuses a foreign cache rather than
trusting a path.  Everything here is that idea with the install as the key.

REFUSAL, NOT DEGRADATION
------------------------
`read_json` returns ``None`` when the stamp does not match, and the caller
must then say so out loud rather than carry on with less.  That is the rule
`coroot.find_derived` already states, with ``meshtex.scan_meshes`` named as
the cautionary tale: every one of the six failures above surfaced as a
*plausible answer*, never as an error, which is precisely what made them
expensive.  A wrong-client index loads as cleanly as a right one.

UNSTAMPED IS NOT FOREIGN, AND THE DIFFERENCE IS THE MIGRATION
-------------------------------------------------------------
An artefact built before this module existed carries no stamp.  That is not
evidence it is wrong -- only that nothing can vouch for it.  The two verdicts
are kept distinct (`UNSTAMPED` vs `FOREIGN`) because every artefact in the
tree is unstamped on the day this lands, and a check that cannot tell "not yet
migrated" from "provably the wrong client" would have to be switched off to be
usable, which is how a gate stops gating.

NO ABSOLUTE PATHS IN A STAMP
----------------------------
The stamp names the install by its *folder name* and fingerprint, never its
path.  ``tests/test_sanitization.py`` forbids absolute user paths in anything
publishable, and derived artefacts get quoted into documentation -- the mixed
``out/dll/rtti.md`` is exactly the kind of file that ends up pasted into a
handoff.  A stamp that leaked ``C:\Users\<name>\...`` would turn this safety
check into a PII hazard.

Pure stdlib, and imports nothing outside COre.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import coroot

__all__ = [
    "SCHEMA", "KEY", "MATCH", "FOREIGN", "UNSTAMPED", "UNKNOWN",
    "stamp", "wrap", "unwrap", "verdict", "describe",
    "orphaned_namespaces", "migration_plan",
    "read_json", "write_json", "sidecar_path", "stamp_file", "read_stamp",
    "Artefact", "audit",
]

#: Bumped only when the stamp's own shape changes incompatibly.  A reader
#: seeing a *newer* schema than it knows refuses, rather than guessing which
#: fields it can still trust.
SCHEMA = 1

#: The envelope key.  Chosen to be unlikely to collide with a payload's own
#: top-level keys; `wrap` asserts it does not.
KEY = "provenance"

MATCH = "match"
FOREIGN = "foreign"
UNSTAMPED = "unstamped"
UNKNOWN = "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_name(tool: str = "") -> str:
    if tool:
        return str(tool)
    try:
        return Path(sys.argv[0]).name or "?"
    except Exception:                                    # pragma: no cover
        return "?"


def _install_name(root=None) -> str:
    """The install's folder name -- never its path.  See the module docstring."""
    try:
        d = Path(root) if root is not None else coroot.game_root()
        return Path(d).name
    except Exception:
        return ""


def stamp(root=None, tool: str = "", base: str = "") -> dict:
    """A provenance record for an artefact derived from ``root``.

    ``base`` lets a caller that already computed `coroot.base_id` pass it in;
    the fingerprint hashes ~37 MB of ``ini/`` (about 50 ms), which is cheap
    once and wasteful in a loop.
    """
    bid = base or coroot.base_id(root)
    kind, _, fp = bid.partition("-")
    return {
        "schema": SCHEMA,
        "base_id": bid,
        "kind": kind,
        "fingerprint": fp,
        "install": _install_name(root),
        "generated": _now(),
        "tool": _tool_name(tool),
    }


def wrap(data: Any, root=None, tool: str = "", base: str = "") -> dict:
    """Put ``data`` in a stamped envelope: ``{provenance: {...}, data: ...}``.

    An envelope rather than extra top-level keys, because several artefacts
    here are keyed by *binary name* at the top level (``out/dll/exports.json``
    is ``{"graphic.dll": ...}``) and a sibling key would be indistinguishable
    from a payload entry -- which is a large part of why those files have no
    room to say what they came from today.
    """
    if isinstance(data, dict) and KEY in data:
        raise ValueError(f"payload already has a {KEY!r} key; refusing to shadow it")
    return {KEY: stamp(root, tool, base), "data": data}


def unwrap(doc: Any) -> tuple[Optional[dict], Any]:
    """Split a document into ``(stamp, payload)``.

    An unstamped document is returned as ``(None, doc)`` unchanged, so a
    reader can migrate gradually instead of failing on everything built
    before this existed.
    """
    if isinstance(doc, dict) and isinstance(doc.get(KEY), dict):
        return doc[KEY], doc.get("data")
    return None, doc


def verdict(doc: Any, root=None, base: str = "") -> str:
    """`MATCH`, `FOREIGN`, `UNSTAMPED` or `UNKNOWN` for a loaded document.

    `UNKNOWN` means *we* cannot say which install is configured -- an
    unreadable or unfingerprintable root -- and is deliberately not `MATCH`:
    `coroot.base_id` already refuses to let a broken install silently share
    whatever was built last, and this keeps that promise on the read side.
    """
    st, _ = unwrap(doc)
    if st is None:
        return UNSTAMPED
    if st.get("schema", 0) > SCHEMA:
        return FOREIGN
    mine = base or coroot.base_id(root)
    if not mine or mine == "unkeyed":
        return UNKNOWN
    return MATCH if st.get("base_id") == mine else FOREIGN


def describe(doc: Any) -> str:
    """A short human summary of where a document came from, for error text."""
    st, _ = unwrap(doc)
    if st is None:
        return "unstamped"
    who = st.get("base_id") or "?"
    when = st.get("generated") or "?"
    tool = st.get("tool") or "?"
    return f"{who}, built {when} by {tool}"


# ---------------------------------------------------------------------------
# JSON artefacts
# ---------------------------------------------------------------------------

def write_json(rel: str, data: Any, root=None, *, tool: str = "",
               base: str = "", indent: int = 2) -> Path:
    """Write a stamped JSON artefact to its keyed location and return the path.

    Resolves through `coroot.derived_path`, so a caller cannot accidentally
    write to the unkeyed literal -- which is how seven ``dump_*`` tools came
    to share one ``out/dll/``.
    """
    p = coroot.derived_path(rel, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = wrap(data, root, tool, base)
    p.write_text(json.dumps(doc, indent=indent), encoding="utf-8")
    return p


def read_json(rel: str, root=None, *, strict: bool = True,
              base: str = "") -> Optional[Any]:
    """Read a stamped JSON artefact, or ``None`` if it cannot be vouched for.

    ``strict`` (the default) refuses anything that is not `MATCH`.  Pass
    ``strict=False`` during migration to accept `UNSTAMPED` -- but never
    `FOREIGN`, which is always a refusal: an artefact that *says* it came from
    another install is the one case where we have positive evidence of the bug
    this module exists to stop.

    Returns the payload, not the envelope.  A caller that gets ``None`` must
    say so out loud rather than continue with less.
    """
    p = coroot.find_derived(rel, root)
    if p is None:
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    v = verdict(doc, root, base)
    if v == MATCH or (not strict and v in (UNSTAMPED, UNKNOWN)):
        return unwrap(doc)[1]
    return None


# ---------------------------------------------------------------------------
# artefacts that are not JSON
# ---------------------------------------------------------------------------

def sidecar_path(path) -> Path:
    """Where the stamp for a non-JSON artefact lives: ``<name>.provenance.json``.

    Markdown reports, ``.png`` thumbnails and binary indexes have nowhere to
    put an envelope, and rewriting them to carry one would change formats that
    other tools parse.  A sidecar keeps the artefact byte-identical.
    """
    p = Path(path)
    return p.with_name(p.name + ".provenance.json")


def stamp_file(path, root=None, *, tool: str = "", base: str = "") -> Path:
    """Write a sidecar stamp beside an existing artefact."""
    sc = sidecar_path(path)
    sc.parent.mkdir(parents=True, exist_ok=True)
    sc.write_text(json.dumps(stamp(root, tool, base), indent=2), encoding="utf-8")
    return sc


def read_stamp(path) -> Optional[dict]:
    """The stamp for ``path`` -- from its envelope if JSON, else its sidecar."""
    p = Path(path)
    sc = sidecar_path(p)
    if sc.is_file():
        try:
            return json.loads(sc.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if p.suffix.lower() == ".json":
        try:
            return unwrap(json.loads(p.read_text(encoding="utf-8")))[0]
        except (OSError, json.JSONDecodeError):
            return None
    return None


# ---------------------------------------------------------------------------
# the audit
# ---------------------------------------------------------------------------

#: Trees the audit does not walk.  Each is runtime output rather than a
#: derived index: capture sessions are per-run, and the thumbnail and log
#: trees hold tens of thousands of files whose identity is already content
#: addressed (`thumbs.py` keys each entry by the hash of the mesh and texture
#: bytes, which is why they were safe by accident).
AUDIT_SKIP = ("out/sessions", "out/companion-logs", "out/thumbs/servers")

#: Artefacts that are legitimately install-independent, with the reason.
#: Anything *not* listed here and not per-base is reported as unclassified --
#: the point of the audit is that this list is currently far too short to
#: account for what is in the tree.
GLOBAL_OK = {
    "out/opcodes.json": "a protocol table, built from refs/, not from an install",
    "out/offsets_cache.json": "self-guarding: keyed on the sha256 of the module bytes",
    "out/health.json": "a report about this checkout, rewritten on every run",
}


class Artefact:
    """One file the audit looked at, and what it could say about it."""

    __slots__ = ("rel", "keyed", "verdict", "stamp", "bytes")

    def __init__(self, rel: str, keyed: bool, v: str,
                 st: Optional[dict], size: int):
        self.rel = rel
        self.keyed = keyed
        self.verdict = v
        self.stamp = st
        self.bytes = size

    @property
    def unclassified(self) -> bool:
        """In a namespace that cannot distinguish installs, and not declared
        install-independent.  This is the fail-open case."""
        return not self.keyed and self.rel not in GLOBAL_OK

    def as_dict(self) -> dict:
        return {"path": self.rel, "keyed": self.keyed, "verdict": self.verdict,
                "unclassified": self.unclassified, "bytes": self.bytes,
                "from": (self.stamp or {}).get("base_id", "")}


def _iter_files(out: Path) -> Iterator[Path]:
    skip = tuple(s.replace("/", os.sep) for s in AUDIT_SKIP)
    for p in out.rglob("*"):
        if not p.is_file() or p.name.endswith(".provenance.json"):
            continue
        s = str(p)
        if any(sep in s for sep in skip):
            continue
        if p.suffix.lower() in (".json", ".md"):
            yield p


def orphaned_namespaces(repo=None, live: str = "") -> list[dict]:
    """Index namespaces under ``out/indexes/`` that no declared install claims.

    A namespace is orphaned when its directory name is not the `base_id` of
    any install the user has declared.  That happens legitimately -- an
    install was deleted or moved -- and it happened *illegitimately* for
    months, because `base_fingerprint` hashed the files a client rewrites at
    shutdown, so merely launching a client stranded its own index and built a
    fresh one beside it.  Three CCO namespaces coexisted that way.

    Reported, never deleted.  These are large (hundreds of MB) and a tool
    that removes them on its own inference is a tool nobody should run.
    """
    repo = Path(repo) if repo is not None else Path(__file__).resolve().parent.parent
    root_dir = repo / coroot.INDEX_ROOT.replace("/", os.sep)
    if not root_dir.is_dir():
        return []
    claimed = set()
    try:
        for decl in (coroot.read_settings().get(coroot.KINDS_KEY) or {}):
            bid = coroot.base_id(decl)
            if bid and bid != "unkeyed":
                claimed.add(bid)
    except Exception:                                    # pragma: no cover
        pass
    if live:
        claimed.add(live)
    out = []
    for d in sorted(root_dir.iterdir()):
        if not d.is_dir() or d.name in claimed:
            continue
        size = 0
        try:
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:                                  # pragma: no cover
            pass
        out.append({"name": d.name, "bytes": size,
                    "path": str(d.relative_to(repo).as_posix())})
    return out


def migration_plan(repo=None) -> list[dict]:
    """Orphaned namespaces that can be *renamed* onto a live one, and why.

    Changing what `coroot.base_fingerprint` hashes re-keys every install
    without changing what any index actually describes: the same install,
    under a new name.  So the old directory is still correct content, and
    renaming it avoids a rebuild that may not even be reproducible -- the
    entity crawl needs a server dump (`artcrawl --sql`) nobody may still have.

    A rename is only offered when it is **unambiguous**: exactly one orphan
    shares the live namespace's kind prefix.  CCO currently has three, so it
    gets ``"ambiguous"`` and a human decides.  That restraint is the same rule
    `coroot.base_id` states -- a missing index means "build me", never "borrow
    another base's answers" -- and borrowing is exactly what a wrong rename
    would be.

    Returns entries with ``action`` in ``rename`` | ``ambiguous`` | ``stale``.
    Plans only; nothing here moves a file.
    """
    repo = Path(repo) if repo is not None else Path(__file__).resolve().parent.parent
    root_dir = repo / coroot.INDEX_ROOT.replace("/", os.sep)
    if not root_dir.is_dir():
        return []
    live = {}
    try:
        for decl in (coroot.read_settings().get(coroot.KINDS_KEY) or {}):
            bid = coroot.base_id(decl)
            if bid and bid != "unkeyed":
                live[bid] = bid.rsplit("-", 1)[0]
    except Exception:                                    # pragma: no cover
        return []
    orphans = [d.name for d in sorted(root_dir.iterdir())
               if d.is_dir() and d.name not in live]
    out = []
    for bid, kind in sorted(live.items()):
        cands = [o for o in orphans if o.rsplit("-", 1)[0] == kind]
        if not cands:
            continue
        exists = (root_dir / bid).is_dir()
        if len(cands) == 1 and not exists:
            out.append({"action": "rename", "from": cands[0], "to": bid,
                        "why": "exactly one orphan of this kind"})
        elif len(cands) == 1 and exists:
            out.append({"action": "stale", "from": cands[0], "to": bid,
                        "why": "the live namespace already exists; the orphan "
                               "is superseded and can be deleted by hand"})
        else:
            out.append({"action": "ambiguous", "from": cands, "to": bid,
                        "why": f"{len(cands)} orphans share this kind -- "
                               "choosing one would be a guess"})
    return out


def audit(repo=None, root=None) -> dict:
    """Walk ``out/`` and report what can and cannot be vouched for.

    Does not read payloads -- only each artefact's stamp and its location --
    so it stays cheap enough to run from `health.py` on every invocation.
    """
    repo = Path(repo) if repo is not None else Path(__file__).resolve().parent.parent
    out = repo / "out"
    base = coroot.base_id(root)
    items: list[Artefact] = []
    if out.is_dir():
        prefix = f"{coroot.INDEX_ROOT}/"
        for p in _iter_files(out):
            rel = p.relative_to(repo).as_posix()
            keyed = rel.startswith(prefix)
            # An artefact under `out/indexes/<base-id>/` is claimed by THAT
            # namespace, not by whichever install happens to be configured.
            # Comparing every keyed artefact against the configured base
            # reported a correctly-filed 6090 index as FOREIGN while 5517 was
            # selected -- a false positive, and the worst possible kind: it
            # is the exact word this audit uses for a real fault, so it would
            # have taught people to disbelieve it.
            expect = base
            if keyed:
                tail = rel[len(prefix):].split("/", 1)
                if tail and tail[0]:
                    expect = tail[0]
            st = read_stamp(p)
            if st is None:
                v = UNSTAMPED
            elif not expect or expect == "unkeyed":
                v = UNKNOWN
            else:
                v = MATCH if st.get("base_id") == expect else FOREIGN
            items.append(Artefact(rel, keyed, v, st, p.stat().st_size))

    foreign = [a for a in items if a.verdict == FOREIGN]
    unclassified = [a for a in items if a.unclassified]
    unstamped = [a for a in items if a.verdict == UNSTAMPED]
    return {
        "base_id": base,
        "scanned": len(items),
        "artefacts": [a.as_dict() for a in items],
        "foreign": [a.rel for a in foreign],
        "unclassified": [a.rel for a in unclassified],
        "unstamped": [a.rel for a in unstamped],
        "orphaned": orphaned_namespaces(repo, base),
        # Foreign is the only *proven* fault. Unstamped is the migration
        # backlog, and unclassified is the fail-open namespace -- both are
        # reported, neither is an error yet. Phase 2 turns the second one red.
        "ok": not foreign,
        "fix": ("Artefacts in an unkeyed namespace cannot say which install "
                "they describe. Build them through `provenance.write_json` "
                "(or stamp them with `provenance.stamp_file`) so a reader can "
                "refuse a foreign one."),
    }


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    coroot.add_root_argument(ap)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    a = ap.parse_args(argv)
    try:
        root = coroot.root_from_args(a)
    except SystemExit:
        raise
    except Exception:
        root = None
    rep = audit(root=root)
    if a.json:
        print(json.dumps(rep, indent=2))
        return 0 if rep["ok"] else 1
    print(f"install    : {rep['base_id']}")
    print(f"scanned    : {rep['scanned']} derived artefact(s) under out/")
    print(f"foreign    : {len(rep['foreign'])}   (built from another install)")
    print(f"unstamped  : {len(rep['unstamped'])}   (cannot be vouched for)")
    print(f"unclassified: {len(rep['unclassified'])}  (unkeyed namespace)")
    for rel in rep["foreign"]:
        print(f"  FOREIGN      {rel}")
    for rel in rep["unclassified"][:40]:
        print(f"  unclassified {rel}")
    if len(rep["unclassified"]) > 40:
        print(f"  ... and {len(rep['unclassified']) - 40} more")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":                               # pragma: no cover
    raise SystemExit(main())
