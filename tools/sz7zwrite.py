r"""sz7zwrite.py -- the 7z WRITER.  Inverse of `tools/sz7z.read_archive`.

WHY THIS EXISTS.  `docs/capability_matrix_2026-09-06.md` grades the map cell
grid "modifiable nowhere", on two blockers.  The second is the one that bites:
from client 5517 the map registry names ``.7z`` on 100% of rows on every
install, so a loose ``.DMap`` beside the archive is a file the client never
opens.  Loose-file override -- how every other asset in this project is
modified -- does not reach maps.  A modified map has to end up INSIDE the
archive the registry names, and until now nothing in this repo could write one.

DESIGN RULE: **byte-exactness first**, the same rule `tools/c3write.py` runs
under.  An archive read by `sz7z.read_archive` and re-emitted by
`serialize_archive` with nothing changed must be byte-identical to the file on
disk.  The gate is `tests/test_7z_roundtrip.py` over every map ``.7z`` on the
box.  A writer that cannot reproduce an untouched archive is not a writer, it
is a re-encoder, and shipping one would silently rewrite a format we only
partly understand.

WHAT IS MODELLED, and it is the whole corpus rather than a convenient subset:
single-folder, single-coder, single-file, PLAIN (unencoded) header, LZMA1
(``030101``) or LZMA2 (``21``).  MEASURED 2026-09-06 over 9,944 map archives
under ``<clients>/*/map/**``: 9,944 match that shape, 0 do not.  Six header
variants in all, differing only in which FilesInfo properties they carry.
Anything outside it raises `sz7z.Sz7zRefused` rather than being guessed at.

THE ONE KNOWN DIFFERENCE FROM 7-ZIP'S OWN OUTPUT.  liblzma's raw LZMA1
encoder appends an end-of-stream marker; 7-Zip's 7z writer does not (MEASURED:
a shipped LZMA1 stream decoded unbounded never reaches ``eof``, and a
liblzma-encoded one is 6 bytes longer and does).  The marker is format-legal
and sits past the last byte a size-bounded decoder reads, so it should be
inert -- but "should be" is not "was seen to be", and confirming it needs a
running client, which this seat does not have.  See
`docs/map_7z_writer_2026-09-06.md`.  For LZMA2 there is no difference: the
shipped streams already end with LZMA2's own 0x00 terminator, and so do ours.

CLI:

    py -3 tools/sz7zwrite.py verify <archive.7z> [...]    # round-trip N files
    py -3 tools/sz7zwrite.py corpus [--limit N]           # the full gate
    py -3 tools/sz7zwrite.py info <archive.7z>            # decoded FilesInfo
    py -3 tools/sz7zwrite.py replace <archive.7z> <payload> --out <path>

`out/` or your worktree only.  `_refuse_target` refuses the clients tree,
Program Files and the configured install root by name.
"""

from __future__ import annotations

import binascii
import lzma
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import sz7z                                                       # noqa: E402
from sz7z import (                                                # noqa: E402
    SIG, Sz7zRefused, kCRC, kCodersUnPackSize, kDummy, kEnd, kFilesInfo,
    kFolder, kHeader, kMainStreamsInfo, kName, kNumUnPackStream, kPackInfo,
    kSize, kSubStreamsInfo, kUnPackInfo, kWinAttributes, kMTime,
)

__all__ = [
    "write_number", "serialize_archive", "replace_payload", "decode_files",
    "roundtrip", "write_archive", "patch_dmap_cells", "Sz7zRefused",
]


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def write_number(value: int) -> bytes:
    r"""7-Zip's `COutArchive::WriteNumber`, byte for byte.

    Reproduced from the reference writer rather than invented, because the
    encoding is not unique: 6195 could be spelled in more than one width and
    only one of those is what 7-Zip emits.  A writer that picked a different
    legal spelling would round-trip through its own reader and still differ
    from the file on disk -- which is exactly the failure the byte-exactness
    gate exists to catch, so the gate is what proves this function right.
    """
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise Sz7zRefused("number out of range: %d" % value)
    first = 0
    mask = 0x80
    for i in range(8):
        if value < (1 << (7 * (i + 1))):
            first |= value >> (8 * i)
            low = value & ((1 << (8 * i)) - 1)
            return bytes([first]) + low.to_bytes(i, "little")
        first |= mask
        mask >>= 1
    return bytes([0xFF]) + value.to_bytes(8, "little")


def _write_bits(flags) -> bytes:
    out = bytearray()
    b = 0
    mask = 0x80
    for f in flags:
        if f:
            b |= mask
        mask >>= 1
        if mask == 0:
            out.append(b); b = 0; mask = 0x80
    if mask != 0x80:
        out.append(b)
    return bytes(out)


def _write_crcs(rec) -> bytes:
    all_defined, crcs = rec
    out = bytearray([kCRC, 1 if all_defined else 0])
    if not all_defined:
        out += _write_bits([c is not None for c in crcs])
    for c in crcs:
        if c is not None:
            out += struct.pack("<I", c)
    return bytes(out)


# ---------------------------------------------------------------------------
# serialize
# ---------------------------------------------------------------------------

def build_header(m: dict) -> bytes:
    """The next-header bytes for a model, in 7-Zip's own field order."""
    h = bytearray([kHeader, kMainStreamsInfo])

    h.append(kPackInfo)
    h += write_number(m["packpos"])
    h += write_number(len(m["packsizes"]))
    h.append(kSize)
    for s in m["packsizes"]:
        h += write_number(s)
    if m["packcrcs"] is not None:
        h += _write_crcs(m["packcrcs"])
    h.append(kEnd)

    h.append(kUnPackInfo)
    h.append(kFolder)
    h += write_number(1)          # one folder; read_archive refuses others
    h.append(0)                   # not external
    h += write_number(1)          # one coder
    c = m["coder"]
    h.append(c["flags"])
    h += c["id"]
    if c["flags"] & 0x20:
        h += write_number(len(c["props"]))
        h += c["props"]
    h.append(kCodersUnPackSize)
    for s in m["unpacksizes"]:
        h += write_number(s)
    if m["foldercrcs"] is not None:
        h += _write_crcs(m["foldercrcs"])
    h.append(kEnd)

    sub = m["substreams"]
    if sub is not None:
        h.append(kSubStreamsInfo)
        if sub["numunpack"] is not None:
            h.append(kNumUnPackStream)
            for n in sub["numunpack"]:
                h += write_number(n)
        if sub["sizes"] is not None:
            raise Sz7zRefused("SubStreamsInfo kSize is not modelled")
        if sub["crcs"] is not None:
            h += _write_crcs(sub["crcs"])
        h.append(kEnd)
    h.append(kEnd)                # end of MainStreamsInfo

    f = m["files"]
    if f is not None:
        h.append(kFilesInfo)
        h += write_number(f["count"])
        for pid, payload in f["props"]:
            h.append(pid)
            h += write_number(len(payload))
            h += payload
        h.append(kEnd)
    h.append(kEnd)                # end of Header
    return bytes(h)


def serialize_archive(m: dict) -> bytes:
    """The whole `.7z` file for a model.

    Byte-identical to the input for an unmodified model; that identity is the
    gate.  The signature header's two CRCs are RECOMPUTED, never carried, so a
    model whose sizes were edited cannot emit a file whose CRCs still describe
    the old one.
    """
    body = bytes(m["pre_gap"]) + bytes(m["packed"]) + bytes(m["post_gap"])
    if len(m["pre_gap"]) != m["packpos"]:
        raise Sz7zRefused("pre_gap length %d does not match packpos %d"
                          % (len(m["pre_gap"]), m["packpos"]))
    if len(m["packed"]) != sum(m["packsizes"]):
        raise Sz7zRefused("packed stream is %d bytes, header declares %d"
                          % (len(m["packed"]), sum(m["packsizes"])))
    hdr = build_header(m)
    tail = struct.pack("<QQI", len(body), len(hdr), binascii.crc32(hdr))
    return (SIG + bytes(m["version"])
            + struct.pack("<I", binascii.crc32(tail)) + tail + body + hdr)


def roundtrip(path) -> tuple:
    """`(ok, detail)` for one archive: read it, write it, compare the bytes."""
    raw = Path(path).read_bytes()
    m = sz7z.read_archive(raw)
    got = serialize_archive(m)
    if got == raw:
        return True, ""
    n = min(len(got), len(raw))
    where = next((i for i in range(n) if got[i] != raw[i]), n)
    return False, ("len %d!=%d diff@0x%X got=%s want=%s"
                   % (len(got), len(raw), where,
                      got[where:where + 8].hex(), raw[where:where + 8].hex()))


# ---------------------------------------------------------------------------
# FilesInfo, decoded
# ---------------------------------------------------------------------------

def decode_files(m: dict) -> list:
    """`[{'name', 'mtime', 'attrib'}, ...]` from the FilesInfo properties.

    Only what the map archives actually carry is decoded.  A property with
    `all_defined == 0` carries a bit vector this does not read, and says so by
    raising -- no archive in the corpus has one.
    """
    f = m["files"]
    if f is None:
        return []
    out = [{"name": None, "mtime": None, "attrib": None}
           for _ in range(f["count"])]
    for pid, payload in f["props"]:
        if pid == kDummy:
            continue
        if pid == kName:
            if payload[:1] != b"\x00":
                raise Sz7zRefused("external kName")
            names = payload[1:].decode("utf-16-le").split("\x00")
            names = [n for n in names[:-1]] if names and names[-1] == "" \
                else names
            for i, n in enumerate(names[:f["count"]]):
                out[i]["name"] = n
        elif pid in (kMTime, kWinAttributes):
            if payload[0] != 1:
                raise Sz7zRefused("property 0x%02X is not all-defined" % pid)
            if payload[1] != 0:
                raise Sz7zRefused("external property 0x%02X" % pid)
            body = payload[2:]
            width = 8 if pid == kMTime else 4
            for i in range(f["count"]):
                v = int.from_bytes(body[i * width:(i + 1) * width], "little")
                out[i]["mtime" if pid == kMTime else "attrib"] = v
    return out


# ---------------------------------------------------------------------------
# modify
# ---------------------------------------------------------------------------

def _filters(coder_id: bytes, props: bytes) -> list:
    """The liblzma filter chain a coder id + props describes.

    Shared by the encode and the verify-decode so they cannot drift: a writer
    that encoded with one dictionary and checked with another would report a
    pass for a stream the client cannot read.
    """
    cid = coder_id.hex()
    if cid == "030101":
        if len(props) != 5:
            raise Sz7zRefused("LZMA1 props are %d bytes, expected 5"
                              % len(props))
        d0 = props[0]
        return [{"id": lzma.FILTER_LZMA1,
                 "dict_size": int.from_bytes(props[1:5], "little"),
                 "lc": d0 % 9, "lp": (d0 // 9) % 5, "pb": d0 // 45}]
    if cid == "21":
        if len(props) != 1:
            raise Sz7zRefused("LZMA2 props are %d bytes, expected 1"
                              % len(props))
        v = props[0]
        if v > 40:
            raise Sz7zRefused("bad LZMA2 dict byte %d" % v)
        ds = 0xFFFFFFFF if v == 40 else ((2 | (v & 1)) << (v // 2 + 11))
        return [{"id": lzma.FILTER_LZMA2, "dict_size": ds}]
    raise Sz7zRefused("coder %s cannot be encoded by this module" % cid)


def replace_payload(m: dict, data: bytes, keep_eos: bool = True) -> dict:
    """A NEW model whose single file is `data`, re-compressed in place.

    The coder and its properties are carried over unchanged, so the archive
    keeps the exact dictionary size and lc/lp/pb the client's decoder is
    handed today; only the stream, the two sizes and the CRC move.

    `keep_eos=True` (the default) leaves liblzma's end-of-stream marker on an
    LZMA1 stream.  The alternative -- trimming back to the shape 7-Zip emits
    -- means guessing how many of the trailing bytes are marker and how many
    are the range coder's flush, and a range decoder READS AHEAD, so a trim
    that satisfies liblzma can starve a different decoder.  Keeping bytes is
    the failure that cannot corrupt; see the module docstring.

    The result is VERIFIED before it is returned: the stream is decoded back
    with the same filter chain, bounded by the declared size, and must equal
    `data`.  An encoder that silently produced something else would otherwise
    be discovered by the client, which is the one place we cannot look.
    """
    if m["pre_gap"] or m["post_gap"]:
        raise Sz7zRefused(
            "archive has %d byte(s) before and %d after the packed stream; "
            "this module will not rebuild a layout it cannot account for"
            % (len(m["pre_gap"]), len(m["post_gap"])))
    if m["files"] is None or m["files"]["count"] != 1:
        raise Sz7zRefused("replace_payload models a one-file archive")
    if len(m["packsizes"]) != 1 or len(m["unpacksizes"]) != 1:
        raise Sz7zRefused("more than one packed or unpacked stream")
    if m["packcrcs"] is not None:
        # A packed-stream CRC would have to be recomputed over the NEW stream.
        # No map archive carries one, so this is refused rather than written
        # on a path nothing exercises.
        raise Sz7zRefused("archive carries a PackInfo CRC (not modelled)")

    filt = _filters(m["coder"]["id"], m["coder"]["props"])
    try:
        c = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=filt)
        packed = c.compress(bytes(data)) + c.flush()
    except lzma.LZMAError as e:
        raise Sz7zRefused("liblzma refused these coder properties (%s): %s"
                          % (m["coder"]["props"].hex(), e))
    if not keep_eos:
        packed = _trim_eos(packed, bytes(data), filt)

    d = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt)
    back = d.decompress(packed, max_length=len(data))
    if back != bytes(data):
        raise Sz7zRefused("re-encoded stream does not decode back to the "
                          "payload (%d vs %d bytes)" % (len(back), len(data)))

    out = dict(m)
    out["packed"] = packed
    out["packsizes"] = [len(packed)]
    out["unpacksizes"] = [len(data)]
    out["pre_gap"] = b""
    out["post_gap"] = b""
    crc = binascii.crc32(bytes(data))
    if out["foldercrcs"] is not None:
        out["foldercrcs"] = (out["foldercrcs"][0], [crc])
    if out["substreams"] is not None and out["substreams"]["crcs"] is not None:
        sub = dict(out["substreams"])
        sub["crcs"] = (sub["crcs"][0], [crc])
        out["substreams"] = sub
    return out


def _trim_eos(packed: bytes, data: bytes, filt) -> bytes:
    """Opt-in: drop the trailing bytes liblzma adds that 7-Zip does not.

    Deliberately conservative -- it takes the LARGEST trim that still decodes
    to the exact payload only up to 8 bytes, and refuses to trim at all if the
    very next byte down also decodes, because that would mean it is eating the
    range coder's flush rather than a marker.
    """
    def ok(n):
        try:
            d = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt)
            return d.decompress(packed[:len(packed) - n],
                                max_length=len(data)) == data
        except lzma.LZMAError:
            return False
    n = 0
    while n < 8 and ok(n + 1):
        n += 1
    if n == 8:
        raise Sz7zRefused("8 trailing bytes are all droppable -- refusing to "
                          "guess where the end-of-stream marker starts")
    return packed[:len(packed) - n] if n else packed


# ---------------------------------------------------------------------------
# the DMap side: produce a payload worth putting back
# ---------------------------------------------------------------------------

def patch_dmap_cells(raw: bytes, edits) -> bytes:
    r"""A `.DMap` with `edits` applied to its cell grid, everything else kept.

    `edits` is an iterable of `(x, y, mask, surface, elevation)`; pass `None`
    for a field to leave it as it is.  The grid is fixed-stride, so this
    writes into a COPY of the original bytes rather than re-serializing the
    file -- every header field, portal, layer and trailer byte this repo has
    not fully decoded survives untouched by construction.  That is the same
    argument `c3write` makes for carrying `unknown0` verbatim, applied to a
    format where the undecoded part is most of the file.

    The per-row checksum is recomputed with `core/dmap.row_checksum`, which is
    the routine measured against 17,742 shipped rows -- including the signed
    elevation the `co-stuff` editor gets wrong.  A row nobody edited is not
    recomputed, so an untouched grid comes back byte-identical.
    """
    import dmap as _dmap
    d = _dmap.parse("x.DMap", data=raw, want_cells=True, verify=False)
    cells = list(d.cells)
    rows = set()
    for x, y, mask, surface, elev in edits:
        if not (0 <= x < d.width and 0 <= y < d.height):
            raise Sz7zRefused("cell (%d,%d) is outside %dx%d"
                              % (x, y, d.width, d.height))
        i = y * d.width + x
        old = cells[i]
        cells[i] = (old[0] if mask is None else mask,
                    old[1] if surface is None else surface,
                    old[2] if elev is None else elev)
        rows.add(y)
    out = bytearray(raw)
    stride = d.width * _dmap.CELL_SIZE + 4
    for y in sorted(rows):
        base = _dmap.GRID_OFF + y * stride
        for x in range(d.width):
            m_, s_, e_ = cells[y * d.width + x]
            struct.pack_into("<HHh", out, base + x * _dmap.CELL_SIZE,
                             m_ & 0xFFFF, s_ & 0xFFFF, e_)
        struct.pack_into("<I", out, base + d.width * _dmap.CELL_SIZE,
                         _dmap.row_checksum(cells, y, d.width))
    return bytes(out)


# ---------------------------------------------------------------------------
# writing to disk, and where it is not allowed to
# ---------------------------------------------------------------------------

def _forbidden_roots() -> list:
    """Directories nothing here may ever write inside.

    Same list, and the same reason, as `tools/garment_overlay_build.py`: the
    clients tree is a set of READ-ONLY controls and an install that is written
    to stops being one.  Downloads is added because the owner's untouched
    vendor archives live there.
    """
    import coroot
    out = []
    try:
        out.append(Path(coroot.clients_dir()).resolve())
    except Exception as e:                                     # noqa: BLE001
        # NARROW ENOUGH TO SAY WHY.  A guard that cannot name its first root
        # is reported, not swallowed -- the remaining roots still apply.
        print("sz7zwrite: clients_dir() unavailable (%s); the clients tree is "
              "NOT covered by this guard" % e, file=sys.stderr)
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                "USERPROFILE"):
        v = os.environ.get(env)
        if v:
            p = Path(v)
            out.append((p / "Downloads").resolve() if env == "USERPROFILE"
                       else p.resolve())
    try:
        import coroot as _c
        found = _c.resolve()
        if found and found.path:
            out.append(Path(found.path).resolve())
    except Exception:
        pass
    return out


def _refuse_target(target) -> Path:
    t = Path(target).resolve()
    for bad in _forbidden_roots():
        if t == bad or bad in t.parents:
            raise SystemExit(
                "REFUSED: this would write\n    %s\nwhich is inside\n    %s\n"
                "Nothing here writes into an install, the clients tree or "
                "Downloads. Write to out/ or your worktree and install with\n"
                "    py -3 tools/comod.py --root <install> install" % (t, bad))
    return t


def write_archive(m: dict, out_path) -> Path:
    p = _refuse_target(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(serialize_archive(m))
    return p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _corpus(limit=None):
    import glob
    import coroot
    base = coroot.clients_dir()
    fs = [p for p in glob.glob(str(base) + "/*/**/*.7z", recursive=True)
          if (os.sep + "map" + os.sep) in p.lower()]
    fs.sort()
    return fs[:limit] if limit else fs


def _cmd_survey() -> int:
    r"""What the corpus IS, so the writer's scope is a measurement not a claim.

    Prints the header shape of every map archive on the box, plus the registry
    split that makes any of this necessary.  It is a command and not a note in
    a document because the note goes stale the day a new install is added and
    nothing says so.
    """
    from collections import Counter
    import coroot
    from dmap import load_gamemap
    fs = _corpus()
    if not fs:
        print("SKIP -- no map .7z under %s (nothing was measured)"
              % coroot.clients_dir())
        return 0
    shapes, coders, props, ver = Counter(), Counter(), Counter(), Counter()
    gaps = Counter()
    for p in fs:
        m = sz7z.read_archive(p)
        coders[m["coder"]["id"].hex()] += 1
        ver[bytes(m["version"]).hex()] += 1
        gaps["pre=%d post=%d" % (len(m["pre_gap"]), len(m["post_gap"]))] += 1
        ids = [pid for pid, _ in (m["files"]["props"] if m["files"] else [])]
        for pid in ids:
            props["0x%02X" % pid] += 1
        shapes["%s | files=%d [%s]"
               % (m["coder"]["id"].hex(),
                  m["files"]["count"] if m["files"] else 0,
                  ",".join("%02X" % i for i in ids))] += 1
    print("map archives read : %d  (all of them parsed; a refusal would have "
          "raised)" % len(fs))
    print("signature version : %s" % dict(ver))
    print("coders            : %s" % dict(coders))
    print("bytes outside the three known regions: %s" % dict(gaps))
    print("FilesInfo props   : %s" % dict(props))
    print("\nheader shapes:")
    for k, v in shapes.most_common():
        print("  %6d  %s" % (v, k))

    print("\nregistry split -- the reason a loose .DMap does not work:")
    base = coroot.clients_dir()
    mixed = 0
    print("  %-26s %-18s %6s %6s %6s" % ("install", "registry", "rows",
                                         ".7z", ".DMap"))
    for d in sorted(x for x in base.iterdir() if x.is_dir()):
        rel, rows = load_gamemap(d)
        if not rows:
            continue
        z = sum(1 for x in rows
                if str(x.get("FileName", "")).lower().endswith(".7z"))
        dm_ = sum(1 for x in rows
                  if str(x.get("FileName", "")).lower().endswith(".dmap"))
        mixed += bool(z and dm_)
        print("  %-26s %-18s %6d %5d%% %5d%%"
              % (d.name, rel, len(rows), round(100 * z / len(rows)),
                 round(100 * dm_ / len(rows))))
    print("  installs with a MIXED registry: %d" % mixed)
    return 0


def _cmd_eos(rest) -> int:
    """The one measured difference between our streams and 7-Zip's.

    liblzma's raw LZMA1 encoder appends an end-of-stream marker and 7-Zip's 7z
    writer does not.  This prints the evidence for one archive rather than
    asking anyone to take it on trust: the shipped stream's unbounded decode
    never reaches `eof`, ours does, and ours is longer by the marker.
    """
    fs = rest or _corpus(1)
    if not fs:
        print("SKIP -- no archive given and none on this box")
        return 0
    for p in fs:
        m = sz7z.read_archive(p)
        data = sz7z.extract(p)
        filt = _filters(m["coder"]["id"], m["coder"]["props"])
        c = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=filt)
        ours = c.compress(data) + c.flush()
        d = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt)
        try:
            d.decompress(bytes(m["packed"]))
            shipped_eof = d.eof
        except lzma.LZMAError as e:
            shipped_eof = "LZMAError: %s" % e
        d2 = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt)
        d2.decompress(ours)
        trims = []
        for k in range(0, 9):
            try:
                dd = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt)
                ok = dd.decompress(ours[:len(ours) - k],
                                   max_length=len(data)) == data
            except lzma.LZMAError:
                ok = False
            trims.append("%d:%s" % (k, "ok" if ok else "NO"))
        print("%s\n  coder %s  unpacked %d"
              % (p, m["coder"]["id"].hex(), len(data)))
        print("  shipped packed %d, unbounded decode reaches eof: %s"
              % (sum(m["packsizes"]), shipped_eof))
        print("  ours    packed %d (%+d), unbounded decode reaches eof: %s"
              % (len(ours), len(ours) - sum(m["packsizes"]), d2.eof))
        print("  ours truncated by N bytes still decodes: %s"
              % "  ".join(trims))
        print("  last byte -- shipped %s, ours %s  (LZMA2's own terminator "
              "is 00)" % (bytes(m["packed"])[-1:].hex(), ours[-1:].hex()))
    return 0


def main(argv) -> int:
    if not argv:
        print("usage:\n"
              "  py -3 tools/sz7zwrite.py verify <archive.7z> [...]\n"
              "  py -3 tools/sz7zwrite.py corpus [--limit N]\n"
              "  py -3 tools/sz7zwrite.py survey\n"
              "  py -3 tools/sz7zwrite.py eos [<archive.7z> ...]\n"
              "  py -3 tools/sz7zwrite.py info <archive.7z>\n"
              "  py -3 tools/sz7zwrite.py replace <archive.7z> <payload> "
              "--out <path>")
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "verify":
        bad = 0
        for a in rest:
            ok, why = roundtrip(a)
            print("%-6s %s%s" % ("OK" if ok else "DIFF", a,
                                 "" if ok else "  " + why))
            bad += not ok
        print("RESULT: %s -- %d file(s), %d byte-exact"
              % ("FAIL" if bad else "PASS", len(rest), len(rest) - bad))
        return 1 if bad else 0
    if cmd == "corpus":
        limit = int(rest[rest.index("--limit") + 1]) if "--limit" in rest \
            else None
        fs = _corpus(limit)
        if not fs:
            print("SKIP -- no map .7z archives under %s"
                  % __import__("coroot").clients_dir())
            return 0
        bad = []
        for p in fs:
            ok, why = roundtrip(p)
            if not ok:
                bad.append((p, why))
        for p, why in bad[:20]:
            print("  DIFF %s  %s" % (p, why))
        print("RESULT: %s -- %d archive(s), %d byte-exact, %d differ"
              % ("FAIL" if bad else "PASS", len(fs), len(fs) - len(bad),
                 len(bad)))
        return 1 if bad else 0
    if cmd == "survey":
        return _cmd_survey()
    if cmd == "eos":
        return _cmd_eos(rest)
    if cmd == "info":
        for a in rest:
            m = sz7z.read_archive(a)
            print("%s\n  coder=%s props=%s packed=%d unpacked=%d"
                  % (a, m["coder"]["id"].hex(), m["coder"]["props"].hex(),
                     sum(m["packsizes"]), sum(m["unpacksizes"])))
            for f in decode_files(m):
                print("  file: %-28s mtime=%s attrib=0x%X"
                      % (f["name"], f["mtime"], f["attrib"]))
        return 0
    if cmd == "replace":
        if "--out" not in rest or len(rest) < 2:
            print("usage: sz7zwrite.py replace <archive.7z> <payload> "
                  "--out <path>")
            return 2
        out = rest[rest.index("--out") + 1]
        arc, payload = rest[0], rest[1]
        m = sz7z.read_archive(arc)
        new = replace_payload(m, Path(payload).read_bytes())
        p = write_archive(new, out)
        print("wrote %s  (%d -> %d packed, %d unpacked)"
              % (p, sum(m["packsizes"]), sum(new["packsizes"]),
                 sum(new["unpacksizes"])))
        back = sz7z.extract(p)
        print("re-extracts to %d bytes, identical to the payload: %s"
              % (len(back), back == Path(payload).read_bytes()))
        return 0
    print("unknown command %r" % cmd)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
