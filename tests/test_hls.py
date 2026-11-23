"""HLS：AES-128 解密、错误密钥必须失败、不支持的加密方式要拒绝。"""
import os, shutil, tempfile, unittest

from .support import StaticServer
import downloader as dl


def _read(path):
    with open(path, 'rb') as fh:
        return fh.read()


def _write(path, data):
    with open(path, 'wb') as fh:
        fh.write(data)

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

    def test_byterange_rejected(self):
        pl = (b'#EXTM3U\n#EXT-X-VERSION:4\n#EXT-X-TARGETDURATION:4\n'
              b'#EXT-X-BYTERANGE:100@0\n#EXTINF:4,\nseg0.ts\n#EXT-X-ENDLIST\n')
        srv = StaticServer({'/playlist.m3u8': (pl, 'application/vnd.apple.mpegurl')})
        try:
            ok, msg = dl.hls_download(srv.url('/playlist.m3u8'),
                                      os.path.join(self.tmp, 'br.ts'), threads=1)
            self.assertFalse(ok)
            self.assertIn('BYTERANGE', msg)
        finally:
            srv.close()

    def test_init_map_rejected(self):
        pl = (b'#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:4\n'
              b'#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,\nseg0.ts\n#EXT-X-ENDLIST\n')
        srv = StaticServer({'/playlist.m3u8': (pl, 'application/vnd.apple.mpegurl')})
        try:
            ok, msg = dl.hls_download(srv.url('/playlist.m3u8'),
                                      os.path.join(self.tmp, 'map.ts'), threads=1)
            self.assertFalse(ok)
            self.assertIn('EXT-X-MAP', msg)
        finally:
            srv.close()

    def test_key_hash_recorded_in_meta(self):
        srv = StaticServer({'/playlist.m3u8': (self._playlist(), 'application/vnd.apple.mpegurl'),
                            '/key.bin': (self.key, 'application/octet-stream'),
                            '/seg0.ts': (self.segs[0], 'video/mp2t'),
                            '/seg1.ts': (self.segs[1], 'video/mp2t')})
        captured = {}
        orig = dl._prepare_parts_meta
        def fake(out_path, part_dir, meta):
            captured.update(meta)
            return False
        dl._prepare_parts_meta = fake
        try:
            ok, msg = dl.hls_download(srv.url('/playlist.m3u8'),
                                      os.path.join(self.tmp, 'meta.ts'), threads=2)
            self.assertTrue(ok, msg)
        finally:
            dl._prepare_parts_meta = orig
            srv.close()
        import hashlib
        kh = captured.get('key_hashes') or {}
        self.assertEqual(list(kh.values())[0], hashlib.sha256(self.key).hexdigest())

    def test_key_content_change_purges_parts(self):
        import json
        srv = StaticServer({'/playlist.m3u8': (self._playlist(), 'application/vnd.apple.mpegurl'),
                            '/key.bin': (self.key, 'application/octet-stream'),
                            '/seg0.ts': (self.segs[0], 'video/mp2t'),
                            '/seg1.ts': (self.segs[1], 'video/mp2t')})
        try:
            url = srv.url('/playlist.m3u8')
            variants, segs = dl.parse_m3u8(self._playlist().decode(), url)
            out = os.path.join(self.tmp, 'keychange.ts')
            part_dir = out + '.hls_parts'
            os.makedirs(part_dir, exist_ok=True)
            _write(os.path.join(part_dir, '000000.ts'), b'old-part')
            meta = {'url': url, 'count': len(segs), 'digest': dl.hls_digest(segs),
                    'key_hashes': {srv.url('/key.bin'): 'deadbeef'}, 'mode': 'hls'}
            with open(out + '.parts.json', 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False)
            ok, msg = dl.hls_download(url, out, threads=2)
            self.assertTrue(ok, msg)
            self.assertEqual(_read(out), b''.join(self.plain))
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
