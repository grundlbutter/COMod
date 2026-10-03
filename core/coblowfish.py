"""CO 5018+ Blowfish (CFB64), ported faithfully from refs/ciphers-snippets/1840796.txt.
Block is read as native little-endian u32 pairs (the C# casts the byte buffer to
UInt32*); IVs are byte-swapped per u32 in SetIVs. Standard Blowfish P/S constants."""
import json, struct, pathlib
M = 0xFFFFFFFF
_c = json.loads((pathlib.Path(__file__).with_name('bf_const.json')).read_text())
INIT_P = _c['P']; INIT_S = _c['S']

class Blowfish:
    def __init__(self):
        self.P = list(INIT_P)
        self.S = [INIT_S[i*256:(i+1)*256] for i in range(4)]
        self.enc_iv = bytearray(8); self.dec_iv = bytearray(8)
        self.enc_num = 0; self.dec_num = 0

    def _F(self, x):
        S = self.S
        return ((((S[0][(x>>24)&0xFF] + S[1][(x>>16)&0xFF]) & M) ^ S[2][(x>>8)&0xFF]) + S[3][x&0xFF]) & M

    def _encipher_block(self, L, R):
        P = self.P
        L ^= P[0]
        for i in range(1, 16, 2):
            R = (R ^ self._F(L) ^ P[i]) & M
            L = (L ^ self._F(R) ^ P[i+1]) & M
        R ^= P[17]
        return R, L                      # note the final swap, per the C#

    def generate_key(self, key: bytes):
        self.P = list(INIT_P)
        self.S = [INIT_S[i*256:(i+1)*256] for i in range(4)]
        self.enc_num = self.dec_num = 0
        buf = bytearray(56); buf[:len(key)] = key   # pad/copy into 56-byte buffer
        length = len(key)
        # XOR key bytes (big-endian grouping, cycling over `length`) into P
        pi = 0; icount = 0
        for i in range(18):
            x = 0
            for _ in range(4):
                x = ((x<<8) | buf[pi]) & M
                pi += 1; icount += 1
                if icount == length:
                    icount = 0; pi = 0
            self.P[i] ^= x
        L = R = 0
        i = 0
        while i < 18:
            L, R = self._encipher_block(L, R)
            self.P[i] = L; self.P[i+1] = R; i += 2
        for j in range(4):
            k = 0
            while k < 256:
                L, R = self._encipher_block(L, R)
                self.S[j][k] = L; self.S[j][k+1] = R; k += 2

    def set_ivs(self, enc_iv: bytes, dec_iv: bytes):
        # C# byte-swaps each u32 of the IV (big-endian conversion)
        def swap(b):
            a,c = struct.unpack('<II', b)
            return struct.pack('>II', a, c)
        self.enc_iv = bytearray(swap(enc_iv)); self.dec_iv = bytearray(swap(dec_iv))
        self.enc_num = self.dec_num = 0

    def _cfb(self, data, iv, num, encrypt):
        out = bytearray(len(data))
        iv = bytearray(iv)
        for i, b in enumerate(data):
            if num == 0:
                L, R = struct.unpack('<II', iv)
                L, R = self._encipher_block(L, R)
                iv[:] = struct.pack('<II', L, R)
            c = b ^ iv[num]
            iv[num] = c if encrypt else b
            out[i] = c
            num = (num + 1) & 7
        return bytes(out), iv, num

    def encrypt(self, data):
        o, self.enc_iv, self.enc_num = self._cfb(data, self.enc_iv, self.enc_num, True); return o
    def decrypt(self, data):
        o, self.dec_iv, self.dec_num = self._cfb(data, self.dec_iv, self.dec_num, False); return o

if __name__ == '__main__':
    # zero-key ECB vector on a zero block
    bf = Blowfish(); bf.generate_key(bytes(8))
    L, R = bf._encipher_block(0, 0)
    print('zero-key encipher(0,0) -> L=0x%08x R=0x%08x' % (L, R))
    print('  (standard Blowfish zero/zero ECB = 4EF99745 6198DD78)')
    # round-trip identity: enc then dec with fresh ciphers, same key+IVs
    import os
    key = b'testkey_12345678'; eiv = bytes(range(8)); div = bytes(range(8,16))
    pt = bytes(range(199))
    e = Blowfish(); e.generate_key(key); e.set_ivs(eiv, div)
    d = Blowfish(); d.generate_key(key); d.set_ivs(div, eiv)   # decrypt IV = encrypt IV mirrored
    ct = e.encrypt(pt); rt = d.decrypt(ct)
    print('round-trip identity:', rt == pt, '(len %d)' % len(pt))
