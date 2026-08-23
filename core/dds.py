#!/usr/bin/env python3
r"""
dds.py -- DirectDraw Surface reader and a self-contained block-compression
decoder, written for the Classic Conquer 2.0 asset viewer.

Why not just use Pillow?  Pillow *does* read DXT1/DXT3/DXT5 and it agrees with
this module on every archived texture in this install (see tools/test_viewer.py,
which diffs the two decoders pixel-for-pixel).  But Pillow's DDS support is
partial: it silently declines a number of legacy uncompressed pixel formats and
it has changed behaviour between releases.  A wrong texture decode is invisible
-- it just produces subtly wrong colours -- so the viewer decodes with this
module and uses Pillow only as a cross-check in the tests.

Formats implemented
    BC1 / DXT1      4-colour + 1-bit-alpha block                 VERIFIED
    BC2 / DXT3      explicit 4-bit alpha + 4-colour block        VERIFIED
    BC3 / DXT5      interpolated alpha + 4-colour block          VERIFIED
    uncompressed    any RGB/RGBA/luminance/alpha mask layout at
                    8/16/24/32 bpp, driven by the header masks   VERIFIED
    DX10 header     BC1/BC2/BC3 and R8G8B8A8 UNORM(_SRGB)        inferred
                    (no DX10-header DDS ships in this install)

What ships in this install (measured, see docs/viewer.md):
    c3.wdf     DXT3 x5694
    data.wdf   DXT3 x7382, DXT1 x6219
    loose      DXT1, DXT3, DXT5
so the three block formats cover everything; the uncompressed path exists for
textures a modder might author.

Output is always straight RGBA8, top row first, as `bytes`.

numpy is used when available purely to speed the per-pixel assembly up; the
pure-python path produces byte-identical output and is what the tests compare.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Optional

try:                                    # optional -- see module docstring
    import numpy as _np
except Exception:                       # pragma: no cover - numpy is optional
    _np = None

#: Set `CO_DDS_SCALAR=1` to force the scalar reference decoder.  It exists so
#: the vectorised path's speedup is reproducible by anyone -- run the same
#: benchmark twice, once with it set -- and so a suspected decode bug can be
#: bisected against the reference without editing this file.  The two paths
#: are byte-identical (`tests/test_dds_numpy.py`), so this changes speed only.
SCALAR_ONLY = os.environ.get("CO_DDS_SCALAR", "") not in ("", "0")

#: True when the vectorised block decoder is the one that will actually run.
#: Read by the viewer's health panel; it is a statement about *this process*,
#: not merely about whether numpy imports.
HAVE_NUMPY = _np is not None and not SCALAR_ONLY

# --- DDS_HEADER.dwFlags / DDS_PIXELFORMAT.dwFlags ---------------------------
DDPF_ALPHAPIXELS = 0x1
DDPF_ALPHA = 0x2
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_LUMINANCE = 0x20000

DDSCAPS2_CUBEMAP = 0x200

# DXGI_FORMAT values we understand from a DX10 header
_DXGI_BC = {70: "DXT1", 71: "DXT1", 72: "DXT1",     # BC1 typeless/unorm/srgb
            73: "DXT3", 74: "DXT3", 75: "DXT3",     # BC2
            76: "DXT5", 77: "DXT5", 78: "DXT5"}     # BC3
_DXGI_RGBA8 = {27, 28, 29}                          # R8G8B8A8 typeless/unorm/srgb
_DXGI_BGRA8 = {87, 88, 90, 91}                      # B8G8R8A8 / X8


class DdsError(ValueError):
    pass


@dataclass
class DdsHeader:
    width: int
    height: int
    mipmaps: int
    fourcc: str                 # "DXT1"/"DXT3"/"DXT5", or "" when uncompressed
    pf_flags: int
    rgb_bits: int
    masks: tuple                # (r, g, b, a)
    pitch_or_linear: int
    caps2: int
    data_offset: int            # first byte of the top-level surface
    dxgi_format: int = 0

    @property
    def compressed(self) -> bool:
        return self.fourcc.startswith("DXT")

    @property
    def has_alpha(self) -> bool:
        if self.fourcc in ("DXT3", "DXT5"):
            return True
        if self.fourcc == "DXT1":
            return True                     # 1-bit; may or may not be used
        return bool(self.pf_flags & (DDPF_ALPHAPIXELS | DDPF_ALPHA))

    @property
    def cubemap(self) -> bool:
        return bool(self.caps2 & DDSCAPS2_CUBEMAP)

    def __str__(self) -> str:
        f = self.fourcc or f"RGB{self.rgb_bits}"
        return f"{self.width}x{self.height} {f} mips={self.mipmaps}"


def parse_header(data: bytes) -> DdsHeader:
    """Parse a DDS_HEADER.  VERIFIED against every .dds in the install."""
    if len(data) < 128 or data[:4] != b"DDS ":
        raise DdsError("not a DDS file")
    (size,) = struct.unpack_from("<I", data, 4)
    if size != 124:
        raise DdsError(f"bad DDS_HEADER size {size}")
    height, width = struct.unpack_from("<II", data, 12)
    (pitch_or_linear,) = struct.unpack_from("<I", data, 20)
    (mips,) = struct.unpack_from("<I", data, 28)
    pf_flags, fourcc_raw = struct.unpack_from("<II", data, 80)
    (rgb_bits,) = struct.unpack_from("<I", data, 88)
    masks = struct.unpack_from("<4I", data, 92)
    (caps2,) = struct.unpack_from("<I", data, 112)

    fourcc = ""
    dxgi = 0
    off = 128
    if pf_flags & DDPF_FOURCC:
        fourcc = struct.pack("<I", fourcc_raw).decode("latin-1").strip("\0 ")
        if fourcc == "DX10":
            if len(data) < 148:
                raise DdsError("DX10 header truncated")
            (dxgi,) = struct.unpack_from("<I", data, 128)
            off = 148
            fourcc = _DXGI_BC.get(dxgi, "")
    if width <= 0 or height <= 0:
        raise DdsError(f"implausible dimensions {width}x{height}")
    return DdsHeader(width, height, mips, fourcc, pf_flags, rgb_bits, masks,
                     pitch_or_linear, caps2, off, dxgi)


# ---------------------------------------------------------------------------
# block decoders
# ---------------------------------------------------------------------------

def _rgb565_table():
    """Expand every RGB565 word once; 65536 entries is cheap and removes the
    inner-loop bit twiddling."""
    tab = []
    for v in range(65536):
        r = (v >> 11) & 0x1F
        g = (v >> 5) & 0x3F
        b = v & 0x1F
        tab.append(((r << 3) | (r >> 2),
                    (g << 2) | (g >> 4),
                    (b << 3) | (b >> 2)))
    return tab


_RGB565 = None


def _rgb565():
    global _RGB565
    if _RGB565 is None:
        _RGB565 = _rgb565_table()
    return _RGB565


def _color_block(c0: int, c1: int, punchthrough: bool):
    """The four palette entries of a BC1-style colour block.

    `punchthrough` is True only for BC1: when c0 <= c1 the block switches to a
    3-colour + transparent-black mode.  BC2/BC3 always use the 4-colour mode
    regardless of the c0/c1 ordering -- that is in the spec and getting it wrong
    is one of the classic silent DDS bugs.
    """
    t = _rgb565()
    r0, g0, b0 = t[c0]
    r1, g1, b1 = t[c1]
    if punchthrough and c0 <= c1:
        return ((r0, g0, b0, 255),
                (r1, g1, b1, 255),
                ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255),
                (0, 0, 0, 0))
    return ((r0, g0, b0, 255),
            (r1, g1, b1, 255),
            ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
            ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255))


def _bc_blocks(data: bytes, bw: int, bh: int, bsize: int):
    """The block payload as `(bh*bw, bsize)` uint8, for the numpy path."""
    return _np.frombuffer(data[:bw * bh * bsize],
                          dtype=_np.uint8).reshape(bh * bw, bsize)


def _le(arr, first: int, count: int, dtype):
    """`count` little-endian bytes of every block, as `dtype`.

    A byte-wise assembly rather than a `view()`, because `view` needs the
    block stride to be a multiple of the target itemsize and a DXT1 block is
    8 bytes wide with a 4-byte field at offset 4 -- true here and not in
    general.  Only the wide fields go through this; the 2- and 3-bit index
    fields are unpacked byte-wise below, which is far cheaper.
    """
    out = _np.zeros(arr.shape[0], dtype=dtype)
    for i in range(count):
        out |= arr[:, first + i].astype(dtype) << dtype(8 * i)
    return out


def _unpack_2bit(b4):
    """`(n,4)` bytes of 2-bit indices -> `(n,16)` uint8, pixel order.

    Pixel `i` lives in byte `i//4` at bit `2*(i%4)`, so the whole field comes
    out in four strided writes with no 32-bit shifting at all.
    """
    n = b4.shape[0]
    sel = _np.empty((n, 16), dtype=_np.uint8)
    for j in range(4):
        sel[:, j::4] = (b4 >> (2 * j)) & 3
    return sel


def _expand565(c):
    """RGB565 -> three uint8 planes, the same widening as `_rgb565_table`."""
    r = ((c >> 11) & 0x1F).astype(_np.int32)
    g = ((c >> 5) & 0x3F).astype(_np.int32)
    b = (c & 0x1F).astype(_np.int32)
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _decode_bc_numpy(data: bytes, width: int, height: int, fmt: str) -> bytearray:
    """Vectorised BC1/BC2/BC3.  Byte-identical to `_decode_bc_py`.

    Only called for block-aligned surfaces (every DDS in this install is);
    a ragged width or height falls back to the scalar decoder rather than
    growing a second set of edge rules that could disagree with it.
    """
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    bsize = 8 if fmt == "DXT1" else 16
    blk = _bc_blocks(data, bw, bh, bsize)
    n = blk.shape[0]

    # --- alpha, when the format carries its own ---------------------------
    alpha = None
    coff = 0
    if fmt == "DXT3":
        # 16 nibbles over 8 bytes: pixel 2k is the low nibble of byte k,
        # pixel 2k+1 the high one.  `* 17` widens 4 bits to 8, as above.
        a8 = blk[:, 0:8]
        alpha = _np.empty((n, 16), dtype=_np.uint8)
        alpha[:, 0::2] = a8 & 0xF
        alpha[:, 1::2] = a8 >> 4
        alpha *= 17
        coff = 8
    elif fmt == "DXT5":
        a0 = blk[:, 0].astype(_np.int32)
        a1 = blk[:, 1].astype(_np.int32)
        abits = _le(blk, 2, 6, _np.uint64)
        pal = _np.empty((n, 8), dtype=_np.int32)
        pal[:, 0] = a0
        pal[:, 1] = a1
        wide = a0 > a1
        # Both interpolation tables are computed for every block and selected
        # by the c0/c1 order, which is cheaper than partitioning and cannot
        # drift from the scalar formulas above.
        for i, (p, q) in enumerate(((6, 1), (5, 2), (4, 3), (3, 4), (2, 5), (1, 6)), 2):
            pal[:, i] = _np.where(wide, (p * a0 + q * a1) // 7, 0)
        for i, (p, q) in enumerate(((4, 1), (3, 2), (2, 3), (1, 4)), 2):
            pal[:, i] = _np.where(wide, pal[:, i], (p * a0 + q * a1) // 5)
        pal[:, 6] = _np.where(wide, pal[:, 6], 0)
        pal[:, 7] = _np.where(wide, pal[:, 7], 255)
        sh = (3 * _np.arange(16, dtype=_np.uint64))
        sel = ((abits[:, None] >> sh) & _np.uint64(0x7)).astype(_np.intp)
        alpha = _np.take_along_axis(pal, sel, axis=1).astype(_np.uint8)
        coff = 8

    # --- the four-entry colour palette ------------------------------------
    c0 = _le(blk, coff, 2, _np.uint32)
    c1 = _le(blk, coff + 2, 2, _np.uint32)
    r0, g0, b0 = _expand565(c0)
    r1, g1, b1 = _expand565(c1)

    pal4 = _np.empty((n, 4, 4), dtype=_np.uint8)
    pal4[:, 0, 0], pal4[:, 0, 1], pal4[:, 0, 2] = r0, g0, b0
    pal4[:, 1, 0], pal4[:, 1, 1], pal4[:, 1, 2] = r1, g1, b1
    pal4[:, :, 3] = 255
    if fmt == "DXT1":
        three = c0 <= c1                       # punchthrough / 3-colour mode
        for ch, (x0, x1) in enumerate(((r0, r1), (g0, g1), (b0, b1))):
            pal4[:, 2, ch] = _np.where(three, (x0 + x1) // 2, (2 * x0 + x1) // 3)
            pal4[:, 3, ch] = _np.where(three, 0, (x0 + 2 * x1) // 3)
        pal4[:, 3, 3] = _np.where(three, 0, 255)
    else:
        for ch, (x0, x1) in enumerate(((r0, r1), (g0, g1), (b0, b1))):
            pal4[:, 2, ch] = (2 * x0 + x1) // 3
            pal4[:, 3, ch] = (x0 + 2 * x1) // 3

    sel = _unpack_2bit(blk[:, coff + 4:coff + 8])
    # One flat gather over `n*4` RGBA entries beats `take_along_axis` here:
    # the index array is (n,16) rather than (n,16,4).
    flat = (_np.arange(n, dtype=_np.intp)[:, None] * 4) + sel
    px = pal4.reshape(n * 4, 4)[flat]
    if alpha is not None:
        px[:, :, 3] = alpha

    # (blocks, 16, rgba) -> (h, w, rgba), 4x4 blocks in raster order
    img = (px.reshape(bh, bw, 4, 4, 4)
             .transpose(0, 2, 1, 3, 4)
             .reshape(bh * 4, bw * 4, 4))
    return bytearray(_np.ascontiguousarray(img[:height, :width]).tobytes())


def _decode_bc(data: bytes, width: int, height: int, fmt: str) -> bytearray:
    """Decode BC1/BC2/BC3 to RGBA8.

    numpy does the per-pixel assembly when it is available and the surface is
    block-aligned -- MEASURED 35 ms -> 2.0 ms for a 256x256 DXT3 tile, and
    that decode was the whole of the asset-viewer map load
    (`docs/asset_decode_perf.md`).  The scalar decoder below is the reference:
    `tests/test_dds_numpy.py` asserts the two agree byte-for-byte over the
    shipped corpus and over synthetic blocks for the modes it does not carry.
    """
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    bsize = 8 if fmt == "DXT1" else 16
    need = bw * bh * bsize
    if len(data) < need:
        raise DdsError(f"{fmt}: need {need} bytes of block data, have {len(data)}")
    if HAVE_NUMPY and width % 4 == 0 and height % 4 == 0:
        return _decode_bc_numpy(data, width, height, fmt)
    return _decode_bc_py(data, width, height, fmt)


def _decode_bc_py(data: bytes, width: int, height: int, fmt: str) -> bytearray:
    """Decode BC1/BC2/BC3 to RGBA8.  Pure python; ~35 ms for 256x256."""
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    bsize = 8 if fmt == "DXT1" else 16
    need = bw * bh * bsize
    if len(data) < need:
        raise DdsError(f"{fmt}: need {need} bytes of block data, have {len(data)}")

    out = bytearray(width * height * 4)
    stride = width * 4
    punch = fmt == "DXT1"
    unp = struct.unpack_from

    for by in range(bh):
        for bx in range(bw):
            p = (by * bw + bx) * bsize
            alpha = None
            if fmt == "DXT3":
                (abits,) = unp("<Q", data, p)
                alpha = [(((abits >> (4 * i)) & 0xF) * 17) for i in range(16)]
                p += 8
            elif fmt == "DXT5":
                a0 = data[p]
                a1 = data[p + 1]
                abits = int.from_bytes(data[p + 2:p + 8], "little")
                if a0 > a1:
                    pal = [a0, a1,
                           (6 * a0 + 1 * a1) // 7, (5 * a0 + 2 * a1) // 7,
                           (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                           (2 * a0 + 5 * a1) // 7, (1 * a0 + 6 * a1) // 7]
                else:
                    pal = [a0, a1,
                           (4 * a0 + 1 * a1) // 5, (3 * a0 + 2 * a1) // 5,
                           (2 * a0 + 3 * a1) // 5, (1 * a0 + 4 * a1) // 5,
                           0, 255]
                alpha = [pal[(abits >> (3 * i)) & 0x7] for i in range(16)]
                p += 8

            c0, c1, bits = unp("<HHI", data, p)
            pal4 = _color_block(c0, c1, punch)

            for i in range(16):
                y = by * 4 + (i >> 2)
                if y >= height:
                    continue
                x = bx * 4 + (i & 3)
                if x >= width:
                    continue
                r, g, b, a = pal4[(bits >> (2 * i)) & 3]
                if alpha is not None:
                    a = alpha[i]
                o = y * stride + x * 4
                out[o] = r
                out[o + 1] = g
                out[o + 2] = b
                out[o + 3] = a
    return out


# ---------------------------------------------------------------------------
# uncompressed decoder
# ---------------------------------------------------------------------------

def _mask_shift_scale(mask: int):
    if mask == 0:
        return 0, 0, 0
    shift = (mask & -mask).bit_length() - 1
    bits = bin(mask >> shift).count("1")
    maxv = (1 << bits) - 1
    return shift, bits, maxv


def _decode_uncompressed(data: bytes, hdr: DdsHeader) -> bytearray:
    """Decode a mask-described uncompressed surface (A8R8G8B8, R5G6B5,
    A1R5G5B5, A4R4G4B4, L8, A8, R8G8B8, ...) to RGBA8."""
    bpp = hdr.rgb_bits
    if bpp not in (8, 16, 24, 32):
        raise DdsError(f"unsupported uncompressed depth {bpp}")
    nbytes = bpp // 8
    w, h = hdr.width, hdr.height
    need = w * h * nbytes
    if len(data) < need:
        raise DdsError(f"uncompressed: need {need} bytes, have {len(data)}")

    rm, gm, bm, am = hdr.masks
    lum = bool(hdr.pf_flags & DDPF_LUMINANCE)
    alpha_only = bool(hdr.pf_flags & DDPF_ALPHA) and not (hdr.pf_flags & (DDPF_RGB | DDPF_LUMINANCE))
    if alpha_only and am == 0:
        am = (1 << bpp) - 1
    if lum and rm == 0:
        rm = (1 << bpp) - 1 if am == 0 else ((1 << bpp) - 1) & ~am

    rs, _, rmax = _mask_shift_scale(rm)
    gs, _, gmax = _mask_shift_scale(gm)
    bs, _, bmax = _mask_shift_scale(bm)
    as_, _, amax = _mask_shift_scale(am)

    out = bytearray(w * h * 4)
    o = 0
    for i in range(w * h):
        px = int.from_bytes(data[i * nbytes:(i + 1) * nbytes], "little")
        if alpha_only:
            r = g = b = 255
        elif lum:
            r = g = b = ((px & rm) >> rs) * 255 // rmax if rmax else 0
        else:
            r = ((px & rm) >> rs) * 255 // rmax if rmax else 0
            g = ((px & gm) >> gs) * 255 // gmax if gmax else 0
            b = ((px & bm) >> bs) * 255 // bmax if bmax else 0
        a = ((px & am) >> as_) * 255 // amax if amax else 255
        out[o] = r
        out[o + 1] = g
        out[o + 2] = b
        out[o + 3] = a
        o += 4
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def surface_size(hdr: DdsHeader, level: int = 0) -> tuple[int, int, int]:
    """(width, height, byte size) of a mip level."""
    w = max(1, hdr.width >> level)
    h = max(1, hdr.height >> level)
    if hdr.compressed:
        bsize = 8 if hdr.fourcc == "DXT1" else 16
        return w, h, ((w + 3) // 4) * ((h + 3) // 4) * bsize
    return w, h, w * h * (hdr.rgb_bits // 8)


def decode(data: bytes, level: int = 0) -> tuple[int, int, bytes]:
    """Decode a DDS to (width, height, RGBA8 bytes), top row first.

    `level` selects a mipmap; level 0 is the base surface.  Almost every
    texture in this install declares mipmaps=0 (the engine generates its own),
    so level 0 is normally the only surface present.
    """
    hdr = parse_header(data)
    off = hdr.data_offset
    for lvl in range(level):
        _, _, sz = surface_size(hdr, lvl)
        off += sz
    w, h, sz = surface_size(hdr, level)
    payload = data[off:off + sz]

    if hdr.compressed:
        return w, h, bytes(_decode_bc(payload, w, h, hdr.fourcc))
    if hdr.dxgi_format in _DXGI_RGBA8:
        return w, h, bytes(payload[:w * h * 4])
    if hdr.dxgi_format in _DXGI_BGRA8:
        buf = bytearray(payload[:w * h * 4])
        buf[0::4], buf[2::4] = buf[2::4], buf[0::4]
        return w, h, bytes(buf)
    if hdr.dxgi_format:
        raise DdsError(f"unsupported DXGI_FORMAT {hdr.dxgi_format}")
    if hdr.fourcc:
        raise DdsError(f"unsupported FourCC {hdr.fourcc!r}")
    sub = DdsHeader(w, h, hdr.mipmaps, hdr.fourcc, hdr.pf_flags, hdr.rgb_bits,
                    hdr.masks, hdr.pitch_or_linear, hdr.caps2, hdr.data_offset,
                    hdr.dxgi_format)
    return w, h, bytes(_decode_uncompressed(payload, sub))


def to_png(data: bytes, level: int = 0, max_size: int = 0) -> bytes:
    """Decode a DDS and re-encode as PNG (for the browser).  Requires Pillow
    only for the PNG *encoder*, never for the DDS decode."""
    from PIL import Image
    import io
    w, h, rgba = decode(data, level)
    im = Image.frombytes("RGBA", (w, h), rgba)
    if max_size and (w > max_size or h > max_size):
        im.thumbnail((max_size, max_size), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def encode_png_to_dds(png_bytes: bytes, fourcc: str = "DXT3") -> bytes:
    """Encode an image (any Pillow-readable format) as a DDS in `fourcc`.

    This is the same path comod.py's `import-png` uses -- Pillow's DDS *writer*
    -- kept here so the viewer can produce a staged file without shelling out.
    Round-tripped in tools/test_viewer.py.
    """
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    buf = io.BytesIO()
    im.save(buf, format="DDS", pixel_format=fourcc)
    return buf.getvalue()


def info_dict(data: bytes) -> dict:
    hdr = parse_header(data)
    return {
        "width": hdr.width, "height": hdr.height, "mipmaps": hdr.mipmaps,
        "format": hdr.fourcc or f"uncompressed {hdr.rgb_bits}bpp",
        "fourcc": hdr.fourcc, "has_alpha": hdr.has_alpha,
        "compressed": hdr.compressed, "cubemap": hdr.cubemap,
        "bytes": len(data),
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    for a in sys.argv[1:]:
        d = Path(a).read_bytes()
        hdr = parse_header(d)
        w, h, rgba = decode(d)
        opaque = sum(1 for i in range(3, len(rgba), 4) if rgba[i] == 255)
        print(f"{Path(a).name}: {hdr}  decoded {w}x{h}  "
              f"{opaque * 100 // (w * h)}% fully opaque")
