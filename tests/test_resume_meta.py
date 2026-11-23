"""续传元数据：资源身份（URL/大小/ETag/HLS 摘要）不一致必须清空分片。"""
import hashlib, os, shutil, tempfile, unittest

import downloader as dl


def _write(path, data):
    with open(path, 'wb') as fh:
        fh.write(data)


class ResumeMetaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='st-meta-')
        self.out = os.path.join(self.tmp, 'file.bin')
        self.part = self.out + '.parts'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, meta):
        dl._prepare_parts_meta(self.out, self.part, meta)
        os.makedirs(self.part, exist_ok=True)
        _write(os.path.join(self.part, '00000.part'), b'x' * 10)

    def test_same_resource_kept(self):
        meta = {'url': 'http://x/f', 'size': 100, 'chunk': 10, 'etag': 'W/1'}
        self._seed(meta)
        dl._prepare_parts_meta(self.out, self.part, meta)
        self.assertTrue(os.path.isdir(self.part))

    def test_different_url_purged(self):
        self._seed({'url': 'http://x/a', 'size': 100, 'chunk': 10, 'etag': ''})
        dl._prepare_parts_meta(self.out, self.part,
                               {'url': 'http://x/b', 'size': 100, 'chunk': 10, 'etag': ''})
        self.assertFalse(os.path.isdir(self.part))

    def test_different_size_purged(self):
        self._seed({'url': 'http://x/a', 'size': 100, 'chunk': 10, 'etag': ''})
        dl._prepare_parts_meta(self.out, self.part,
                               {'url': 'http://x/a', 'size': 200, 'chunk': 10, 'etag': ''})
        self.assertFalse(os.path.isdir(self.part))

    def test_different_etag_purged(self):
        self._seed({'url': 'http://x/a', 'size': 100, 'chunk': 10, 'etag': 'v1'})
        dl._prepare_parts_meta(self.out, self.part,
                               {'url': 'http://x/a', 'size': 100, 'chunk': 10, 'etag': 'v2'})
        self.assertFalse(os.path.isdir(self.part))

    def test_hls_digest_mismatch_purged(self):
        d1 = hashlib.sha256(b'http://x/1.ts\nhttp://x/2.ts').hexdigest()
        d2 = hashlib.sha256(b'http://x/1.ts\nhttp://x/3.ts').hexdigest()
        meta = {'url': 'http://x/p.m3u8', 'count': 2, 'digest': d1, 'mode': 'hls'}
        self._seed(meta)
        dl._prepare_parts_meta(self.out, self.part, dict(meta, digest=d2))
        self.assertFalse(os.path.isdir(self.part))

    def test_unknown_parts_without_meta_purged(self):
        os.makedirs(self.part, exist_ok=True)
        _write(os.path.join(self.part, '00000.part'), b'x')
        dl._prepare_parts_meta(self.out, self.part,
                               {'url': 'http://x/f', 'size': 100, 'chunk': 10, 'etag': ''})
        self.assertFalse(os.path.isdir(self.part))
