"""HLS：AES-128 解密、错误密钥必须失败、不支持的加密方式要拒绝。"""
import os, shutil, tempfile, unittest

from .support import StaticServer
import downloader as dl


def _read(path):
    with open(path, 'rb') as fh:
        return fh.read()

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except Exception:
    HAS_CRYPTO = False


def _encrypt(data, key, iv):
    pad = 16 - len(data) % 16
    data = data + bytes([pad]) * pad
    return AES.new(key, AES.MODE_CBC, iv).encrypt(data)


@unittest.skipUnless(HAS_CRYPTO, '需要 pycryptodome')
class HlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='st-hls-')
        self.key = bytes(range(16))
        self.plain = [b'AAAA' * 400, b'BBBB' * 400]
        self.segs = [_encrypt(p, self.key, i.to_bytes(16, 'big'))
                     for i, p in enumerate(self.plain)]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _playlist(self, key_name='key.bin', method='AES-128'):
        return ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n'
                '#EXT-X-MEDIA-SEQUENCE:0\n'
                f'#EXT-X-KEY:METHOD={method},URI="{key_name}"\n'
                '#EXTINF:4,\nseg0.ts\n#EXTINF:4,\nseg1.ts\n#EXT-X-ENDLIST\n').encode()

    def test_aes128_download_decrypts(self):
        srv = StaticServer({'/playlist.m3u8': (self._playlist(), 'application/vnd.apple.mpegurl'),
                            '/key.bin': (self.key, 'application/octet-stream'),
                            '/seg0.ts': (self.segs[0], 'video/mp2t'),
                            '/seg1.ts': (self.segs[1], 'video/mp2t')})
        try:
            out = os.path.join(self.tmp, 'out.ts')
            ok, msg = dl.hls_download(srv.url('/playlist.m3u8'), out, threads=2)
            self.assertTrue(ok, msg)
            self.assertEqual(_read(out), b''.join(self.plain))
        finally:
            srv.close()

    def test_wrong_key_size_fails(self):
        srv = StaticServer({'/playlist.m3u8': (self._playlist(), 'application/vnd.apple.mpegurl'),
                            '/key.bin': (b'shortkey', 'application/octet-stream'),
                            '/seg0.ts': (self.segs[0], 'video/mp2t'),
                            '/seg1.ts': (self.segs[1], 'video/mp2t')})
        try:
            out = os.path.join(self.tmp, 'bad.ts')
            ok, msg = dl.hls_download(srv.url('/playlist.m3u8'), out, threads=2)
            self.assertFalse(ok)
            self.assertIn('解密', msg)
            self.assertFalse(os.path.exists(out))
        finally:
            srv.close()

    def test_unsupported_encryption_rejected(self):
        srv = StaticServer({'/playlist.m3u8': (self._playlist(method='SAMPLE-AES'),
                                               'application/vnd.apple.mpegurl')})
        try:
            ok, msg = dl.hls_download(srv.url('/playlist.m3u8'),
                                      os.path.join(self.tmp, 'x.ts'), threads=2)
            self.assertFalse(ok)
            self.assertIn('不支持', msg)
        finally:
            srv.close()
