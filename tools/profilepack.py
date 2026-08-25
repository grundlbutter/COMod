#!/usr/bin/env python3
r"""
profilepack.py -- PRODUCE and APPLY a pre-bootstrap name profile.

`tools/profilecheck.py` is the acceptance check and answers "should I trust
this?".  This module is the two halves either side of it: **`pack`** turns a
built `out/wdf/` into one distributable file, and **`apply`** puts a verified
one into use.  Read `profilecheck`'s docstring first -- it carries the
argument for why a shipped table can be trusted at all, and nothing here
repeats it.

    py -3 tools/profilepack.py pack                     # build out/profiles/*.zip
    py -3 tools/profilepack.py apply <profile.zip>      # verify; report; do nothing
    py -3 tools/profilepack.py apply <profile.zip> --yes   # verify, then use it
    py -3 tools/profilepack.py apply --undo             # stop using it
    py -3 tools/profilepack.py terms                    # what the artefact says

=============================================================================
WHY THIS IS A SEPARATE ARTEFACT AND NOT A DIRECTORY IN THE REPO
=============================================================================

COMod is MIT licensed, and its safety story is one sentence that is currently
true **without qualification**:

    the tool ships no game data; every path it reads points into an install
    the user already has.

A name profile is the first thing that would put an asterisk on that
sentence, so the two questions are kept apart *structurally* rather than by
a paragraph asking people to remember the difference:

  * **The code is MIT.**  This module, `profilecheck.py`, `wdf_recover.py`.
    They contain no recovered names -- they contain the search that finds
    them and the check that verifies them.
  * **The profile is data, and is NOT covered by that grant.**  MEASURED in
    the profile built on 2026-08-24: **24,655 recovered paths** (10,216 c3 +
    14,439 data).  That is a substantial compilation extracted from someone
    else's binaries.  Per `out/wdf/name_recovery_summary.json`, **98% of them
    came from `from_observed_strings`** -- strings the client itself ships,
    read off disk and then confirmed by hash -- and the rest from pattern
    enumeration.  That is a *harvest*, not an authorship, and calling it MIT
    would be claiming a grant over something this project never had rights
    in.  See `TERMS_TEXT` below, which travels inside every profile.

**What mechanically keeps them apart** -- three existing mechanisms, no new
one invented:

  1. `.gitignore` line 4 ignores `out/` entire, and a profile is only ever
     written under `out/profiles/`.  This stops the accident.
  2. `tests/test_sanitization.py` **check 4** (`name_table_findings`, run
     over `publishable_files()` from that file's `main`) scans **every file
     `git push` would carry** for a recovered name table and fails on one.
     By CONTENT, not filename -- a rename does not defeat it -- and it reads
     inside `.zip`, which is the shipping shape.  This stops `git add -f`,
     which overrides an ignore rule silently: the same reasoning as
     `local_list_is_tracked` in that file, and the same failure it was
     written for.
  3. `tools/extract_comod.py` is a strict *allowlist*: nothing reaches the
     published COMod tree unless it is named there, and a data file is not.
     `profilepack` is named (it is code); no profile is.

So the repo cannot carry one by accident, cannot carry one by `-f`, and
cannot publish one even if it did.

=============================================================================
OPT-IN MEANS THE USER CHOOSES
=============================================================================

Nothing here downloads anything -- there is no fetch in this module and no
URL in it, deliberately.  A profile arrives because a person went and got it.

`apply` without `--yes` **verifies and then does nothing**, printing what it
would do.  That is the default, and it is the default in the direction that
costs nothing to be wrong about.  With `--yes` it writes two overrides into
the user's own config through `coroot.set_derived_override`, which is the
existing mechanism the override gate already guards: from that moment
`coroot.find_derived` re-verifies the table on every read and fails CLOSED if
it cannot look.  `--undo` clears them.

The reason `apply` refuses on anything but ACCEPT is that this is the one
moment where the user's install is present and the answer is cheap.
MEASURED on Clients/5517: the full three-part check is **0.37 s** against a
`wdf_recover` rebuild of ~34 min.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import coroot                                            # noqa: E402
import profilecheck                                      # noqa: E402
import provenance                                        # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: Where a produced profile is written.  Under `out/`, which `.gitignore`
#: ignores whole -- mechanism 1 in the docstring.  Not configurable, because
#: a configurable output directory is how a data file ends up in a source
#: tree, and the one thing this module must never do is make that easy.
PROFILE_DIR = "out/profiles"

#: The profile format's own version, separate from `provenance.SCHEMA`.
#: Bumped when the zip's *layout* changes, not when a stamp field moves.
PROFILE_SCHEMA = 1

#: The terms file carried INSIDE every profile.
TERMS_NAME = "TERMS.md"
#: Written beside the unpacked tables so `--if-needed` can tell "this exact
#: profile is already active" from "some profile once was".
APPLIED_STAMP = "applied.json"

#: The overrides `apply` sets, as `coroot` spells them.  Derived from
#: `profilecheck.ARCHIVES` rather than written out, so teaching the verifier
#: a third table does not leave `apply` quietly setting two.
def override_rels() -> "list[str]":
    """``["out/wdf/c3_names.json", ...]`` -- one per archive in the domain."""
    return [f"out/wdf/{fn}" for _stem, fn in profilecheck.ARCHIVES]


#: **The artefact's own terms, travelling inside it.**
#:
#: `refs/LICENSE_CONFLICTS.md` exists because "vendoring is a two-minute act
#: whose consequences surface months later, at publication".  A file that
#: says nothing about where it came from is exactly that act.  So this text
#: is not a courtesy -- it is the thing that makes the artefact legible to
#: whoever finds it detached from this repository, which is the state every
#: distributed file eventually reaches.
#:
#: Deliberately a MEASUREMENT and a disclaimer of rights, never a grant:
#: this project has no rights in TQ Digital's assets and therefore cannot
#: license anything about them, and the text must not imply otherwise.
TERMS_TEXT = """\
# Conquer Online name profile -- what this is, and what it is not

**This is not legal advice.** It is a description of how this file was made,
so that whoever holds it can decide what to do with it.

## What it is

A lookup table: `{32-bit hash -> asset path}`, for the entries of a Conquer
Online `c3.wdf` / `data.wdf` archive. Roughly 24,000 pairs.

The archives store each file under a hash of its path and do not store the
path itself. This table is the inverse of that hash, recovered by search.

## What it was derived from

**Overwhelmingly, from strings the client already ships.** The producing tool
reads printable strings out of the client's own files, hashes each candidate,
and keeps the ones whose hash matches an archive entry. Measured on the build
this format was designed against: **98% of recovered names came from observed
strings**, the remainder from enumerating patterns over names already found.

Every pair in this file is therefore either a string that was present in the
client, or a mechanically generated variation of one -- and in both cases the
pair was **confirmed by forward hash** before being written down.

## What it does NOT contain

**No asset bytes.** No textures, models, sounds, maps, or archive contents of
any kind. This file is inert without the reader's own copy of the game: it
names entries, and names nothing that is not already inside an archive the
reader must supply themselves.

## Rights

**This file grants you no rights in anyone's assets, and claims none.**

The tool that produced it is MIT licensed. **This data file is not covered by
that grant and is not offered under it.** The project that built it holds no
rights in TQ Digital Entertainment's client, its archives, or the asset paths
named here, and therefore cannot and does not license them to you. The MIT
notice on the *software* says nothing about this *data*, and the two were
deliberately kept in separate artefacts so that neither is mistaken for the
other.

Whether you may hold or use this file is a question about your own copy of
the game and your jurisdiction. That question is yours; nothing here answers
it for you.

## Verifying it

Do not trust this file because of this notice -- a notice is a claim, not a
check. Verify it against your own installation:

    py -3 tools/profilecheck.py --profile <this-profile>.zip

That re-hashes every name to its key (exact), and checks every key against an
entry your own archive holds (exact). Coverage is reported as a **measured
floor, never as proof of completeness**: whether a better search would have
found more is the one question only recomputation answers.
"""

#: A one-line form for the manifest, so a machine reading the JSON is not
#: told less than a human reading the Markdown.
TERMS_SUMMARY = (
    "Recovered {hash -> asset path} pairs, ~98% harvested from strings the "
    "client itself ships and all confirmed by forward hash. Contains NO asset "
    "bytes and is inert without your own archives. The producing tool is MIT; "
    "THIS DATA IS NOT COVERED BY THAT GRANT. It conveys no rights in anyone's "
    "assets and claims none. See TERMS.md inside this profile.")

#: Fixed zip member timestamps, so the archive carries no build-machine mtime.
#:
#: **This does NOT make the blob reproducible, and an earlier draft of this
#: comment claimed it did.**  Measured: two packs of identical tables gave
#: `c3a493b8788403` and `7cf16eaa85e2b9`.  The cause is `provenance.stamp`,
#: which timestamps every stamp -- so the manifest differs and the blob hash
#: with it.  That is provenance working as designed, not a defect to remove.
#:
#: The consequence is that **a blob hash cannot answer "did we build the same
#: thing?"**, so `content_sha256` below does: it covers the name tables only,
#: excluding the manifest, and IS stable across builds.  Two producers can
#: compare that; comparing blob hashes would show a spurious difference every
#: time and teach people to ignore the check.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

_ABS_PATH_RE = re.compile(r"[A-Za-z]:[\\/]|^[\\/]{2}", re.MULTILINE)


def content_sha256(tables: dict) -> str:
    """A hash over the NAME TABLES only -- stable across builds.

    The blob hash is not, because the manifest is timestamped (see
    `_ZIP_EPOCH`).  This is the one two producers can compare to decide
    whether their searches found the same thing.
    """
    h = hashlib.sha256()
    for stem, fn in profilecheck.ARCHIVES:
        tab = tables.get(stem)
        if tab is None:
            continue
        h.update(fn.encode("utf-8"))
        h.update(json.dumps(tab, sort_keys=True,
                            separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()[:16]


def manifest_for(root=None, repo=None, counts=None) -> dict:
    """The profile's manifest: a provenance stamp, the producer, the terms.

    `base_id` and `producer` are the two keys `profilecheck.verdict` reads.
    Everything else is for a human, and none of it is trusted by the check.
    """
    m = dict(provenance.stamp(root, tool="profilepack.py"))
    m["profile_schema"] = PROFILE_SCHEMA
    m["producer"] = profilecheck.producer_id(repo)
    m["producer_inputs"] = list(profilecheck.PRODUCER_INPUTS)
    m["tables"] = dict(counts or {})
    m["terms"] = TERMS_SUMMARY
    m["terms_file"] = TERMS_NAME
    m["note"] = profilecheck.PRODUCER_NOTE
    return m


def manifest_absolute_paths(manifest: dict) -> "list[str]":
    """Any absolute path in the manifest -- which would make it unportable.

    `provenance.stamp` already reduces the install to its FOLDER NAME, and
    that is what makes a stamp shippable at all.  This re-checks the whole
    assembled manifest rather than trusting that one function, because the
    manifest gained fields here that `provenance` never saw.
    """
    bad = []
    for k, v in manifest.items():
        for s in (v if isinstance(v, list) else [v]):
            if isinstance(s, str) and _ABS_PATH_RE.search(s):
                bad.append(f"{k}={s}")
    return bad


def profile_name(manifest: dict) -> str:
    """``patch5517-76c7f449-a1b2c3d4e5f6.zip`` -- base and producer, both.

    The producer is IN THE FILENAME and not only in the manifest so that two
    profiles for the same client built by different searches cannot overwrite
    one another on the way to a user.
    """
    return f"{manifest.get('base_id') or 'unknown'}-{manifest.get('producer') or 'noproducer'}.zip"


def pack(out_dir=None, root=None, repo=None, dest=None,
         require_sound: bool = True) -> dict:
    """Build a profile zip from a built ``out/wdf/``.

    Refuses to write an UNSOUND table.  Packing is the last moment the
    producing install is guaranteed present, the check costs a fraction of a
    second, and shipping a table that fails its own verifier is the one
    outcome with no recovery -- every recipient pays to discover it.
    """
    repo_dir = Path(repo) if repo is not None else REPO
    tables, _man, where = profilecheck.load_tables(None, out_dir)
    if not tables:
        return {"ok": False, "why": f"no name tables at {where}"}

    root = root or coroot.game_root()
    rep = profilecheck.check(root, tables)
    unsound = sum(a.get("unsound", 0) for a in rep["archives"].values()
                  if a.get("checked"))
    named = sum(a.get("verified_named", 0) for a in rep["archives"].values()
                if a.get("checked"))
    if require_sound and unsound:
        return {"ok": False, "why": f"REFUSED to pack: {unsound} names do not "
                                    f"hash to their own key", "report": rep}
    if require_sound and not named:
        return {"ok": False, "why": "REFUSED to pack: no shipped name names an "
                                    "entry this install holds -- these tables "
                                    "are not for this client", "report": rep}

    counts = {stem: {"names": len(tab)} for stem, tab in tables.items()}
    man = manifest_for(root, repo_dir, counts)
    man["content_sha256"] = content_sha256(tables)
    bad = manifest_absolute_paths(man)
    if bad:
        # Never write an unportable stamp. A manifest naming the producer's
        # own disk is a privacy leak that ships to every recipient.
        return {"ok": False, "why": f"REFUSED to pack: manifest carries an "
                                    f"absolute path: {bad}"}

    target_dir = Path(dest) if dest is not None else (repo_dir / PROFILE_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / profile_name(man)

    members = [(TERMS_NAME, TERMS_TEXT.encode("utf-8")),
               ("manifest.json",
                json.dumps(man, indent=2, sort_keys=True).encode("utf-8"))]
    for stem, fn in profilecheck.ARCHIVES:
        if stem in tables:
            members.append((fn, json.dumps(tables[stem], sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(members):
            zi = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
    blob = buf.getvalue()
    out_path.write_bytes(blob)

    return {"ok": True, "path": str(out_path), "bytes": len(blob),
            "raw_bytes": sum(len(d) for _n, d in members),
            "manifest": man, "report": rep,
            "sha256": hashlib.sha256(blob).hexdigest()[:16]}


def _shipped_terms(profile) -> str:
    """The `TERMS.md` a profile actually carries.

    Falls back to a NOTICE saying the file carried none, never to this
    tool's own text: a profile without terms is a fact about that profile,
    and substituting ours would manufacture a provenance claim for an
    artefact that made none.
    """
    try:
        with zipfile.ZipFile(Path(profile)) as z:
            if TERMS_NAME in z.namelist():
                return z.read(TERMS_NAME).decode("utf-8")
    except (OSError, ValueError, zipfile.BadZipFile):
        pass
    return ("# No terms shipped with this profile\n\n"
            "This profile carried no " + TERMS_NAME + ". That is unusual and "
            "worth asking about: a name profile is data derived from a game "
            "client, it is NOT covered by the producing tool's MIT licence, "
            "and it conveys no rights in anyone's assets. Nothing here can "
            "tell you who built it or from what.\n\n"
            "Verify it against your own installation before trusting it:\n\n"
            "    py -3 tools/profilecheck.py --profile <profile>.zip\n")


def apply(profile, root=None, yes: bool = False, dest=None) -> dict:
    """Verify a profile against the user's own install; with ``yes``, use it.

    Without ``yes`` this is a REPORT and writes nothing anywhere.  That is
    the opt-in: the default action of the apply command is to not apply.
    """
    p = Path(profile)
    if not p.is_file():
        return {"ok": False, "why": f"no such profile: {p}"}
    tables, man, _where = profilecheck.load_tables(str(p))
    if not tables:
        return {"ok": False, "why": f"{p.name} carries no name tables"}
    shipped_terms = _shipped_terms(p)

    root = root or coroot.game_root()
    t0 = time.perf_counter()
    rep = profilecheck.check(root, tables)
    v = profilecheck.verdict(rep, man, root)
    v["seconds"] = round(time.perf_counter() - t0, 3)

    # The profile's own summary if it carries one. A profile that declares
    # nothing is SAID to declare nothing, rather than being read back this
    # tool's boilerplate as though the artefact had claimed it.
    res = {"ok": bool(v["accept"]), "verdict": v, "manifest": man,
           "report": rep, "applied": False,
           "terms": man.get("terms")
           or "this profile's manifest declares NO terms -- see "
              f"{TERMS_NAME} inside it, and treat it as unattributed data"}
    if not v["accept"]:
        res["why"] = "REFUSED by profilecheck -- not applied"
        return res
    if not yes:
        res["why"] = ("verified, NOT applied. Re-run with --yes to use it.")
        return res

    # Explicit act from here down: unpack beside the profile's own base id,
    # then point the two overrides at those files.
    dest_dir = (Path(dest) if dest is not None
                else REPO / PROFILE_DIR / "applied" / str(man.get("base_id") or "unknown"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, fn in profilecheck.ARCHIVES:
        if stem not in tables:
            continue
        f = dest_dir / fn
        f.write_text(json.dumps(tables[stem], sort_keys=True,
                                separators=(",", ":")), encoding="utf-8")
        written.append(str(f))
    # THE TERMS THAT SHIPPED, not this tool's current ones. An earlier draft
    # wrote `TERMS_TEXT` here, which silently re-labels somebody else's
    # artefact with the running version's text -- the one thing a terms file
    # must never do, in the module whose whole purpose is that an artefact
    # states its own provenance.
    (dest_dir / TERMS_NAME).write_text(shipped_terms, encoding="utf-8")
    # What `already_applied` reads. The CONTENT hash, not the blob hash --
    # the blob is timestamped and varies per build, so comparing it would
    # report "different profile" for a rebuild of identical tables.
    (dest_dir / APPLIED_STAMP).write_text(json.dumps(
        {"content_sha256": man.get("content_sha256"),
         "base_id": man.get("base_id")}, indent=2), encoding="utf-8")

    set_rels = []
    for rel in override_rels():
        f = dest_dir / Path(rel).name
        if f.is_file():
            coroot.set_derived_override(rel, f)
            set_rels.append(rel)

    res.update({"applied": True, "dest": str(dest_dir), "files": written,
                "overrides": set_rels,
                "why": "applied; coroot.find_derived re-verifies on every read"})
    return res


def already_applied(profile) -> bool:
    """Is THIS profile's content already the active override?

    Compares the profile's `content_sha256` against a stamp written beside the
    unpacked files, and requires every override to still RESOLVE -- so a
    cleared override, a deleted file, or a different profile all read as "no".

    This exists so a launcher can call `apply --yes --if-needed` on every
    start without re-verifying and re-writing 1 MB each time. It is a
    shortcut around work already done, never around the verification: when it
    answers "no", the full checked path runs.
    """
    p = Path(profile)
    if not p.is_file():
        return False
    try:
        _tables, man, _w = profilecheck.load_tables(str(p))
    except Exception:
        return False
    want = man.get("content_sha256")
    if not want:
        return False
    cur = coroot.derived_overrides()
    for rel in override_rels():
        f = cur.get(rel)
        if not f or not Path(f).is_file():
            return False
    stamp = Path(cur[override_rels()[0]]).parent / APPLIED_STAMP
    if not stamp.is_file():
        return False
    try:
        return json.loads(stamp.read_text(encoding="utf-8")).get("content_sha256") == want
    except Exception:
        return False


def undo() -> dict:
    """Clear the overrides `apply` set.  Leaves the unpacked files on disk."""
    cleared = []
    cur = coroot.derived_overrides()
    for rel in override_rels():
        if rel in cur:
            coroot.set_derived_override(rel, None)
            cleared.append(rel)
    return {"ok": True, "cleared": cleared}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="produce and apply a pre-bootstrap name profile",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p_pack = sub.add_parser("pack", help="build a profile from out/wdf/")
    p_pack.add_argument("--out-dir", default=None,
                        help="a directory of *_names.json (default: out/wdf/)")
    p_pack.add_argument("--root", default=None, help="the producing install")
    p_pack.add_argument("--dest", default=None, help="where to write the zip")

    p_ap = sub.add_parser("apply", help="verify a profile, and with --yes use it")
    p_ap.add_argument("profile", nargs="?", default=None)
    p_ap.add_argument("--root", default=None, help="the install to check against")
    p_ap.add_argument("--yes", action="store_true",
                      help="actually use it. Without this, apply only reports.")
    p_ap.add_argument("--dest", default=None, help="where to unpack")
    p_ap.add_argument("--undo", action="store_true",
                      help="stop using any applied profile")
    p_ap.add_argument("--if-needed", action="store_true", dest="if_needed",
                      help="do nothing if this profile is already applied. "
                           "For launchers that run apply on every start.")

    sub.add_parser("terms", help="print the terms a profile carries")
    for q in (p_pack, p_ap):
        q.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "terms":
        print(TERMS_TEXT)
        return 0

    if a.cmd == "pack":
        r = pack(a.out_dir, a.root, None, a.dest)
        if a.json:
            print(json.dumps(r, indent=2))
        elif r["ok"]:
            print(f"wrote {r['path']}")
            print(f"  {r['bytes']:,} bytes zipped, {r['raw_bytes']:,} raw "
                  f"({100.0 * r['bytes'] / max(r['raw_bytes'], 1):.1f}%)")
            print(f"  base_id  {r['manifest']['base_id']}")
            print(f"  producer {r['manifest']['producer']}")
            print(f"  sha256   {r['sha256']} (blob; varies per build -- "
                  f"the manifest is timestamped)")
            print(f"  content  {r['manifest']['content_sha256']} (tables only; "
                  f"STABLE -- compare this one)")
            print(f"  carries  {TERMS_NAME} -- this data is NOT under the "
                  f"tool's MIT grant")
        else:
            print(r["why"], file=sys.stderr)
        return 0 if r["ok"] else 1

    if a.cmd == "apply":
        if a.undo:
            r = undo()
            print(f"cleared {len(r['cleared'])} override(s): "
                  f"{', '.join(r['cleared']) or 'none were set'}")
            return 0
        if not a.profile:
            print("apply needs a profile path (or --undo)", file=sys.stderr)
            return 2
        if a.if_needed and already_applied(a.profile):
            if a.json:
                print(json.dumps({"ok": True, "applied": False,
                                  "why": "already applied -- nothing to do"}))
            else:
                print("  profile already applied -- nothing to do")
            return 0
        r = apply(a.profile, a.root, a.yes, a.dest)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r["ok"] else 1
        v = r.get("verdict")
        if v:
            for c in v["clauses"]:
                print(f"  {c['clause']:>16}: {c['says']}")
            print(f"\n  {'VERDICT':>16}: "
                  f"{'ACCEPT' if v['accept'] else 'REFUSE'} in {v['seconds']}s")
        print(f"\n  {'TERMS':>16}: {r.get('terms')}")
        print(f"  {'RESULT':>16}: {r.get('why')}")
        if r.get("applied"):
            for rel in r["overrides"]:
                print(f"  {'override':>16}: {rel} -> {r['dest']}")
        return 0 if r["ok"] else 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
