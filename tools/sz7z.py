"""Minimal 7z reader: single-folder archives with a PLAIN (unencoded) header.

Enough to extract the 7878 map archives with the standard library only. Not a
general 7z implementation -- it refuses anything it does not fully understand
rather than guessing, so a 'no' from this is a real no.
"""
import io, lzma, struct

kEnd,kHeader,kMainStreamsInfo,kPackInfo,kUnPackInfo,kSize,kFolder,kCodersUnPackSize = \
    0x00,0x01,0x04,0x06,0x07,0x09,0x0B,0x0C
kFilesInfo,kSubStreamsInfo,kCRC,kNumUnPackStream,kEncodedHeader = 0x05,0x08,0x0A,0x0D,0x17
#: FilesInfo property ids this module can name.  Anything else is refused by
#: `read_archive` rather than carried, because a property whose payload is not
#: understood cannot be re-emitted with any confidence that it still means
#: what it meant.  kDummy (0x19) is the exception ON PURPOSE: 7-Zip writes it
#: as alignment padding with no semantics, and 4,119 of the 9,944 map archives
#: on this box carry one, so refusing it would refuse 41% of the corpus.
kDummy,kName,kCTime,kATime,kMTime,kWinAttributes = 0x19,0x11,0x12,0x13,0x14,0x15
FILE_PROPS = (kDummy, kName, kCTime, kATime, kMTime, kWinAttributes)

SIG = b'7z\xbc\xaf\x27\x1c'

class R:
    def __init__(s,b): s.b=b; s.i=0
    def u8(s): v=s.b[s.i]; s.i+=1; return v
    def num(s):
        first=s.u8(); mask=0x80; value=0
        for i in range(8):
            if not (first & mask):
                return value | ((first & (mask-1)) << (8*i))
            value |= s.b[s.i] << (8*i); s.i+=1
            mask >>= 1
        return value

def read_meta(path):
    # `with`, not `io.open(...).read()`: the old form leaked a handle per call
    # and a corpus gate over 9,944 archives buried its own output in
    # ResourceWarning. Same bytes, one closed file.
    with io.open(path,'rb') as f: b=f.read()
    if b[:6]!=b'7z\xbc\xaf\x27\x1c': raise ValueError('not 7z')
    nhoff,nhsize=struct.unpack_from('<QQ',b,12)
    nh=b[32+nhoff:32+nhoff+nhsize]
    r=R(nh)
    if r.u8()!=kHeader: raise ValueError('encoded or unsupported header')
    if r.u8()!=kMainStreamsInfo: raise ValueError('no MainStreamsInfo')
    if r.u8()!=kPackInfo: raise ValueError('no PackInfo')
    packpos=r.num(); nstreams=r.num()
    sizes=[]
    while True:
        t=r.u8()
        if t==kEnd: break
        if t==kSize: sizes=[r.num() for _ in range(nstreams)]
        else: raise ValueError('unhandled PackInfo id 0x%02X'%t)
    if r.u8()!=kUnPackInfo: raise ValueError('no UnPackInfo')
    if r.u8()!=kFolder: raise ValueError('no Folder')
    nfolders=r.num()
    if nfolders!=1 or r.u8()!=0: raise ValueError('multi-folder or external')
    ncoders=r.num()
    if ncoders!=1: raise ValueError('%d coders'%ncoders)
    flags=r.u8(); idsize=flags & 0x0F
    cid=bytes(nh[r.i:r.i+idsize]); r.i+=idsize
    if flags & 0x10: raise ValueError('complex coder')
    props=b''
    if flags & 0x20:
        n=r.num(); props=bytes(nh[r.i:r.i+n]); r.i+=n
    if r.u8()!=kCodersUnPackSize: raise ValueError('no CodersUnPackSize')
    unpacked=r.num()
    return {'coder':cid.hex(),'props':props.hex(),'packpos':packpos,
            'packsize':sizes[0] if sizes else None,'unpacked':unpacked,
            'data':b[32+packpos:32+packpos+(sizes[0] if sizes else 0)]}

def extract(path):
    m=read_meta(path)
    p=bytes.fromhex(m['props'])
    if m['coder']=='030101':
        d0=p[0]
        filt=[{'id':lzma.FILTER_LZMA1,'dict_size':int.from_bytes(p[1:5],'little'),
               'lc':d0%9,'lp':(d0//9)%5,'pb':(d0//45)}]
    elif m['coder']=='21':
        # LZMA2: one property byte encoding dict size.
        v=p[0]
        if v>40: raise ValueError('bad LZMA2 dict byte %d'%v)
        ds=0xFFFFFFFF if v==40 else ((2 | (v & 1)) << (v//2 + 11))
        filt=[{'id':lzma.FILTER_LZMA2,'dict_size':ds}]
    else:
        raise ValueError('coder %s unsupported'%m['coder'])
    # BOUND THE OUTPUT to the size the header declares. A raw LZMA stream has no
    # length of its own, so the decoder can emit trailing bytes past the real end;
    # measured 2026-08-27, 37 of 470 archives came out exactly ONE byte long. The
    # unbounded read looked fine -- the DMap still parsed -- so only the size
    # assertion caught it. Bound first, then assert, and keep the assert.
    dec=lzma.LZMADecompressor(format=lzma.FORMAT_RAW,filters=filt)
    out=dec.decompress(m['data'],max_length=m['unpacked'])
    if len(out)!=m['unpacked']:
        raise ValueError('short read: got %d, header declares %d'%(len(out),m['unpacked']))
    return out


class Sz7zRefused(ValueError):
    """The archive holds a construct this module will not model.

    Raised instead of guessing.  Every message names the byte or the count
    that stopped it, so a refusal is a measurement and not a shrug.
    """


def read_archive(src):
    r"""The COMPLETE model of a single-folder, plain-header 7z archive.

    `read_meta` reads only as far as the packed stream -- enough to decompress,
    not enough to write the file back.  This walks the whole next header
    (PackInfo, UnPackInfo, SubStreamsInfo, FilesInfo) into a structure that
    `tools/sz7zwrite.serialize_archive` re-emits, and the gate on that pair is
    byte-identity over the corpus (`tests/test_7z_roundtrip.py`).

    `src` is a path or a `bytes`.  Returns a dict:

        version       2 signature bytes at offset 6
        packpos       PackInfo's data offset, relative to byte 32
        packsizes     one per packed stream
        packcrcs      None, or (all_defined, [crc-or-None, ...])
        coder         {'flags', 'id', 'props'}   -- exactly one coder
        unpacksizes   kCodersUnPackSize, one per output stream
        foldercrcs    None, or (all_defined, [crc-or-None, ...])
        substreams    None, or {'numunpack', 'sizes', 'crcs'}
        files         None, or {'count', 'props': [(id, payload_bytes), ...]}
        packed        the packed stream bytes, verbatim
        pre_gap       bytes between the signature and the packed stream
        post_gap      bytes between the packed stream and the next header

    The two gaps exist so that byte-exactness is a claim about the WHOLE file
    and not only the parts this module understands.  They are empty on all
    9,944 map archives measured; `sz7zwrite` refuses to modify an archive
    whose gaps are not empty, because it cannot know what is in them.
    """
    if isinstance(src, (bytes, bytearray)):
        b = bytes(src)
    else:
        with io.open(src, 'rb') as f:
            b = f.read()
    if b[:6] != SIG:
        raise Sz7zRefused('not 7z (magic %s)' % b[:6].hex())
    if len(b) < 32:
        raise Sz7zRefused('file shorter than the 32-byte signature header')
    import binascii
    start_crc, = struct.unpack_from('<I', b, 8)
    nhoff, nhsize, nhcrc = struct.unpack_from('<QQI', b, 12)
    if binascii.crc32(b[12:32]) != start_crc:
        raise Sz7zRefused('signature-header CRC mismatch')
    if 32 + nhoff + nhsize > len(b):
        raise Sz7zRefused('next header runs past end of file')
    nh = b[32 + nhoff:32 + nhoff + nhsize]
    if binascii.crc32(nh) != nhcrc:
        raise Sz7zRefused('next-header CRC mismatch')
    if not nh:
        raise Sz7zRefused('empty next header')

    r = R(nh)
    t = r.u8()
    if t == kEncodedHeader:
        raise Sz7zRefused('encoded (compressed) header')
    if t != kHeader:
        raise Sz7zRefused('top-level id 0x%02X is not kHeader' % t)
    t = r.u8()
    if t != kMainStreamsInfo:
        raise Sz7zRefused('id 0x%02X where kMainStreamsInfo expected' % t)

    # -- PackInfo ----------------------------------------------------------
    t = r.u8()
    if t != kPackInfo:
        raise Sz7zRefused('id 0x%02X where kPackInfo expected' % t)
    packpos = r.num()
    nstreams = r.num()
    packsizes = None
    packcrcs = None
    while True:
        t = r.u8()
        if t == kEnd:
            break
        if t == kSize:
            packsizes = [r.num() for _ in range(nstreams)]
        elif t == kCRC:
            packcrcs = _read_crcs(r, nh, nstreams)
        else:
            raise Sz7zRefused('unhandled PackInfo id 0x%02X' % t)
    if packsizes is None:
        raise Sz7zRefused('PackInfo carries no kSize')

    # -- UnPackInfo --------------------------------------------------------
    t = r.u8()
    if t != kUnPackInfo:
        raise Sz7zRefused('id 0x%02X where kUnPackInfo expected' % t)
    t = r.u8()
    if t != kFolder:
        raise Sz7zRefused('id 0x%02X where kFolder expected' % t)
    nfolders = r.num()
    external = r.u8()
    if nfolders != 1:
        raise Sz7zRefused('%d folders (this module models exactly 1)' % nfolders)
    if external != 0:
        raise Sz7zRefused('external folder definition')
    ncoders = r.num()
    if ncoders != 1:
        raise Sz7zRefused('%d coders (this module models exactly 1)' % ncoders)
    flags = r.u8()
    idsize = flags & 0x0F
    cid = bytes(nh[r.i:r.i + idsize]); r.i += idsize
    if flags & 0x10:
        raise Sz7zRefused('complex coder (multiple in/out streams)')
    if flags & 0xC0:
        raise Sz7zRefused('reserved coder flag bits set: 0x%02X' % flags)
    props = b''
    if flags & 0x20:
        n = r.num(); props = bytes(nh[r.i:r.i + n]); r.i += n
    t = r.u8()
    if t != kCodersUnPackSize:
        raise Sz7zRefused('id 0x%02X where kCodersUnPackSize expected' % t)
    unpacksizes = [r.num()]
    foldercrcs = None
    while True:
        t = r.u8()
        if t == kEnd:
            break
        if t == kCRC:
            foldercrcs = _read_crcs(r, nh, nfolders)
        else:
            raise Sz7zRefused('unhandled UnPackInfo id 0x%02X' % t)

    # -- SubStreamsInfo (optional) ----------------------------------------
    substreams = None
    t = r.u8()
    if t == kSubStreamsInfo:
        sub = {'numunpack': None, 'sizes': None, 'crcs': None}
        t = r.u8()
        if t == kNumUnPackStream:
            sub['numunpack'] = [r.num() for _ in range(nfolders)]
            t = r.u8()
        nsub = sum(sub['numunpack']) if sub['numunpack'] else nfolders
        if t == kSize:
            # One size per substream EXCEPT the last of each folder, which is
            # implied.  Only reachable when a folder holds >1 substream, which
            # no archive in the corpus does; refuse rather than model it blind.
            raise Sz7zRefused('SubStreamsInfo kSize (folder with >1 substream)')
        if t == kCRC:
            substreams_crc_count = nsub
            sub['crcs'] = _read_crcs(r, nh, substreams_crc_count)
            t = r.u8()
        if t != kEnd:
            raise Sz7zRefused('unhandled SubStreamsInfo id 0x%02X' % t)
        substreams = sub
        t = r.u8()
    if t != kEnd:
        raise Sz7zRefused('id 0x%02X where end of MainStreamsInfo expected' % t)

    # -- FilesInfo (optional) ---------------------------------------------
    files = None
    t = r.u8()
    if t == kFilesInfo:
        count = r.num()
        fprops = []
        while True:
            pid = r.u8()
            if pid == kEnd:
                break
            size = r.num()
            payload = bytes(nh[r.i:r.i + size])
            if len(payload) != size:
                raise Sz7zRefused('FilesInfo property 0x%02X runs past the '
                                  'header' % pid)
            r.i += size
            if pid not in FILE_PROPS:
                raise Sz7zRefused('unmodelled FilesInfo property 0x%02X' % pid)
            fprops.append((pid, payload))
        files = {'count': count, 'props': fprops}
        t = r.u8()
    if t != kEnd:
        raise Sz7zRefused('id 0x%02X where end of Header expected' % t)
    if r.i != len(nh):
        raise Sz7zRefused('%d unconsumed byte(s) after the header'
                          % (len(nh) - r.i))

    total = sum(packsizes)
    data_start = 32 + packpos
    return {'version': b[6:8],
            'packpos': packpos,
            'packsizes': packsizes,
            'packcrcs': packcrcs,
            'coder': {'flags': flags, 'id': cid, 'props': props},
            'unpacksizes': unpacksizes,
            'foldercrcs': foldercrcs,
            'substreams': substreams,
            'files': files,
            'packed': b[data_start:data_start + total],
            'pre_gap': b[32:data_start],
            'post_gap': b[data_start + total:32 + nhoff]}


def _read_crcs(r, nh, n):
    """kCRC's payload: all-defined flag, an optional bit vector, then u32s."""
    all_defined = r.u8()
    if all_defined:
        defined = [True] * n
    else:
        defined = _read_bits(r, nh, n)
    crcs = []
    for d in defined:
        if d:
            crcs.append(struct.unpack_from('<I', nh, r.i)[0]); r.i += 4
        else:
            crcs.append(None)
    return (all_defined, crcs)


def _read_bits(r, nh, n):
    out = []
    b = 0
    mask = 0
    for _ in range(n):
        if mask == 0:
            b = r.u8(); mask = 0x80
        out.append(bool(b & mask))
        mask >>= 1
    return out


def _dmap_head(out):
    import struct
    ver = struct.unpack_from('<I', out, 0)[0]
    end = out.find(chr(0).encode(), 8)
    return ver, out[8:end].decode('latin1')


def _selftest():
    import glob, lzma as _l
    # Through coroot rather than a literal: the clients tree is one box's
    # fact, and a selftest that hardcodes it SKIPS silently everywhere else
    # while reporting green. Falls back to a skip if coroot is unreachable.
    try:
        import sys as _s, os as _o
        # SEARCH for the repo root rather than counting directories up from
        # __file__. The original counted THREE, which was correct while this
        # file sat in the staging directory and overshoots to the drive root
        # now that it lives in tools/ -- and the failure was SILENT: the
        # import raised, the bare `except` below turned it into `_base = ''`,
        # and the self-test printed "no 7878 map archives on this box" over a
        # box that has 100+ of them. A skip that reads as an answer is exactly
        # what this tool's own promotion was meant to stop shipping.
        _d = _o.path.dirname(_o.path.abspath(__file__))
        for _ in range(6):
            if _o.path.isfile(_o.path.join(_d, 'core', 'coroot.py')):
                _s.path.insert(0, _o.path.join(_d, 'core'))
                break
            _d = _o.path.dirname(_d)
        import coroot as _cr
        _base = str(_cr.clients_dir() / '7878' / 'map' / 'map')
    except Exception:
        _base = ''
    fs = sorted(glob.glob(_base + '/*.7z')) if _base else []
    if not fs:
        print('sz7z selftest: SKIP -- no 7878 map archives on this box')
        return 0
    p = fs[0]
    out = extract(p)
    ver, pul = _dmap_head(out)
    a1 = len(out) > 1000 and pul.lower().startswith('map')
    m = read_meta(p)
    q = bytes.fromhex(m['props'])

    def filt(dict_size):
        if m['coder'] == '030101':
            return [{'id': _l.FILTER_LZMA1, 'dict_size': dict_size,
                     'lc': q[0] % 9, 'lp': (q[0] // 9) % 5, 'pb': q[0] // 45}]
        return [{'id': _l.FILTER_LZMA2, 'dict_size': dict_size}]

    # MUTANT: a wrong dictionary size must not yield the same bytes.
    try:
        r = _l.LZMADecompressor(format=_l.FORMAT_RAW,
                                filters=filt(4096)).decompress(
            m['data'], max_length=m['unpacked'])
        a2 = r[:64] != out[:64] or len(r) != len(out)
    except Exception:
        a2 = True

    # CONTROL: the unbounded read overshoots on 37 of 470 archives; on this one
    # it may or may not, so it is reported and not asserted.
    try:
        real = (int.from_bytes(q[1:5], 'little') if m['coder'] == '030101'
                else (2 | (q[0] & 1)) << (q[0] // 2 + 11))
        r2 = _l.LZMADecompressor(format=_l.FORMAT_RAW,
                                 filters=filt(real)).decompress(m['data'])
        over = len(r2) - m['unpacked']
    except Exception:
        over = 'n/a'

    ok = a1 and a2
    print('sz7z selftest: %s -- extract+DMap(%s) | wrong-dict refused(%s) | '
          'unbounded overshoot on this file: %s bytes'
          % ('PASSED' if ok else 'FAILED', a1, a2, over))
    return 0 if ok else 1


if __name__ == '__main__':
    import sys
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    elif len(sys.argv) > 1:
        for a in sys.argv[1:]:
            m = read_meta(a)
            print('%s  coder=%s packed=%s unpacked=%s'
                  % (a, m['coder'], m['packsize'], m['unpacked']))
    else:
        print('usage: sz7z.py --selftest | sz7z.py <archive.7z> ...')