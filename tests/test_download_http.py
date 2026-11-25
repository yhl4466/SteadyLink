"""HTTP 下载：Range 校验、回退单流、分片合并与大小校验。"""
import json, os, tempfile, shutil, threading, unittest

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

    def test_fetch_chunk_detects_etag_change(self):
        srv = RangeServer(self.data, 'range', etag_mode='change')
        try:
            info = dl.probe(srv.url)          # 第 1 次请求：ETag v1
            part = os.path.join(self.tmp, 'p.part')
            r = dl._fetch_chunk(srv.url, part, 0, 1023, None,
                                threading.Event(), lambda n: None, retries=1,
                                etag=info.get('etag', ''))
            self.assertEqual(r, 'changed')    # 分片请求时服务端已变成 v2
        finally:
            srv.close()

    def test_fetch_chunk_detects_ifrange_mismatch(self):
        srv = RangeServer(self.data, 'range')
        try:
            part = os.path.join(self.tmp, 'p.part')
            r = dl._fetch_chunk(srv.url, part, 0, 1023, None,
                                threading.Event(), lambda n: None, retries=1,
                                etag='"other"')
            self.assertEqual(r, 'changed')
        finally:
            srv.close()

    def test_download_recovers_after_resource_change(self):
        srv = RangeServer(self.data, 'range', etag_mode='change')
        try:
            out = os.path.join(self.tmp, 'changed.bin')
            ok, msg = dl.download(srv.url, out, threads=2, chunk=64 * 1024)
            self.assertTrue(ok, msg)          # 检测到变化 -> 重新探测后重下一次
            self.assertEqual(_read(out), self.data)
        finally:
            srv.close()

    def test_fallback_when_range_disappears_after_probe(self):
        # 探测时支持 Range，分片下载时服务器却不支持了 -> 必须回退单流且不崩
        srv = RangeServer(self.data, 'range_then_plain', send_etag=False)
        try:
            out = os.path.join(self.tmp, 'range_gone.bin')
            ok, msg = dl.download(srv.url, out, threads=2, chunk=64 * 1024)
            self.assertTrue(ok, msg)
            self.assertEqual(_read(out), self.data)
        finally:
            srv.close()

    def test_single_retry_does_not_crash(self):
        # 预置半截文件与匹配的元数据，模拟续传失败后的递归重试分支
        out = os.path.join(self.tmp, 'single.bin')
        _write(out, b'partial')
        srv = RangeServer(self.data, 'range')
        try:
            url = srv.url.replace('/file', '/missing')
            with open(out + '.parts.json', 'w', encoding='utf-8') as f:
                json.dump({'url': url, 'mode': 'single', 'etag': '', 'last_modified': ''}, f)
            ok, msg = dl._download_single(url, out, None, None,
                                          threading.Event(), None, etag='', last_modified='')
            self.assertFalse(ok)
        finally:
            srv.close()

    def test_auto_chunk_size(self):
        self.assertEqual(dl.auto_chunk_size(5 * 1024 * 1024), 5 * 1024 * 1024)
        self.assertEqual(dl.auto_chunk_size(30 * 1024 * 1024), 2 * 1024 * 1024)
        self.assertEqual(dl.auto_chunk_size(200 * 1024 * 1024), 4 * 1024 * 1024)
        self.assertEqual(dl.auto_chunk_size(None), 4 * 1024 * 1024)

    def test_aria2_http_backend(self):
        if not dl.aria2_path():
            self.skipTest('缺少 aria2c')
        srv = RangeServer(self.data, 'range')
        try:
            out = os.path.join(self.tmp, 'aria.bin')
            ok, msg = dl.http_download_aria2(srv.url, out, threads=4)
            self.assertTrue(ok, msg)
            self.assertEqual(_read(out), self.data)
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
