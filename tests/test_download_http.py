"""HTTP 下载：Range 校验、回退单流、分片合并与大小校验。"""
import os, tempfile, shutil, threading, unittest

from .support import RangeServer
import downloader as dl


def _read(path):
    with open(path, 'rb') as fh:
        return fh.read()


def _write(path, data):
    with open(path, 'wb') as fh:
        fh.write(data)


class HttpRangeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='st-dl-')
        self.data = bytes((i * 37 + 11) % 251 for i in range(300_000))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_chunk_ok(self):
        srv = RangeServer(self.data, 'range')
        try:
            part = os.path.join(self.tmp, 'p.part')
            r = dl._fetch_chunk(srv.url, part, 1000, 1000 + 4095, None,
                                threading.Event(), lambda n: None, retries=1)
            self.assertEqual(r, 'ok')
            self.assertEqual(os.path.getsize(part), 4096)
            self.assertEqual(_read(part), self.data[1000:1000 + 4096])
        finally:
            srv.close()

    def test_fetch_chunk_norange(self):
        srv = RangeServer(self.data, 'norange')
        try:
            part = os.path.join(self.tmp, 'p.part')
            r = dl._fetch_chunk(srv.url, part, 0, 1023, None,
                                threading.Event(), lambda n: None, retries=1)
            self.assertEqual(r, 'norange')
        finally:
            srv.close()

    def test_fetch_chunk_bad_content_range(self):
        srv = RangeServer(self.data, 'badcr')
        try:
            part = os.path.join(self.tmp, 'p.part')
            r = dl._fetch_chunk(srv.url, part, 0, 1023, None,
                                threading.Event(), lambda n: None, retries=1)
            self.assertEqual(r, 'error')
        finally:
            srv.close()

    def test_download_fallback_to_single_stream(self):
        srv = RangeServer(self.data, 'norange')
        try:
            out = os.path.join(self.tmp, 'fallback.bin')
            ok, msg = dl.download(srv.url, out, threads=8)
            self.assertTrue(ok, msg)
            self.assertEqual(_read(out), self.data)
            self.assertFalse(os.path.exists(out + '.parts.json'))
        finally:
            srv.close()

    def test_segmented_download_merges_correctly(self):
        srv = RangeServer(self.data, 'range')
        try:
            out = os.path.join(self.tmp, 'seg.bin')
            ok, msg = dl.download(srv.url, out, threads=4, chunk=64 * 1024)
            self.assertTrue(ok, msg)
            self.assertEqual(_read(out), self.data)
            self.assertFalse(os.path.isdir(out + '.parts'))
            self.assertFalse(os.path.exists(out + '.parts.json'))
        finally:
            srv.close()

    def test_resume_ignores_parts_from_other_url(self):
        # 伪造别的 URL 留下的分片（内容错误），续传时必须被丢弃
        out = os.path.join(self.tmp, 'resume.bin')
        part_dir = out + '.parts'
        os.makedirs(part_dir, exist_ok=True)
        _write(os.path.join(part_dir, '00000.part'), b'\x00' * 1024)
        dl._prepare_parts_meta(out, part_dir, {'url': 'http://other/file',
                                               'size': len(self.data), 'chunk': 64 * 1024})
        srv = RangeServer(self.data, 'range')
        try:
            ok, msg = dl.download(srv.url, out, threads=2, chunk=64 * 1024)
            self.assertTrue(ok, msg)
            self.assertEqual(_read(out), self.data)
        finally:
            srv.close()
