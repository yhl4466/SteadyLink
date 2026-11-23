"""测试公共设施：临时 profile、假节点、本地 HTTP/HLS 服务器。"""
import json, os, re, sys, tempfile, threading, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import v2core as core
import downloader as dl

HAS_XRAY = os.path.exists(os.path.join(ROOT, 'bin', 'xray.exe'))


def fake_node(n=1, security='tls'):
    host = f'example{n}.com'
    link = (f'vless://11111111-1111-1111-1111-11111111111{n}@{host}:443'
            f'?type=tcp&security={security}&sni=example.com#test{n}')
    return core.parse_node(link)


class TempProfile:
    """把 v2core 的运行时目录重定向到临时目录，避免污染仓库。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='steadylink-test-')
        self._saved = {}
        for name in ('CFG_DIR', 'NODES_FILE', 'CONFIG_FILE', 'USAGE_FILE', 'HISTORY_FILE',
                     'PROXY_BACKUP'):
            self._saved[name] = getattr(core, name, None)
        core.CFG_DIR = os.path.join(self.dir, 'runtime')
        core.NODES_FILE = os.path.join(self.dir, 'nodes.json')
        core.CONFIG_FILE = os.path.join(self.dir, 'config.json')
        core.USAGE_FILE = os.path.join(self.dir, '用量统计.json')
        core.HISTORY_FILE = os.path.join(self.dir, '用量历史.csv')
        core.PROXY_BACKUP = os.path.join(core.CFG_DIR, 'proxy_backup.json')
        os.makedirs(core.CFG_DIR, exist_ok=True)
        self.cfg = dict(core.DEFAULT_CONFIG)
        self.cfg.update({'http_port': 0, 'socks_port': 0, 'stats_port': 0,
                         'access_log': False, 'core_log_level': 'warning'})

    def free_ports(self, n=3):
        import socket
        socks = []
        for _ in range(n):
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            socks.append(s)
        ports = [s.getsockname()[1] for s in socks]
        for s in socks:
            s.close()
        return ports

    def config(self):
        h, s, st = self.free_ports()
        c = dict(self.cfg)
        c.update({'http_port': h, 'socks_port': s, 'stats_port': st})
        return c

    def close(self):
        for name, val in self._saved.items():
            if val is not None:
                setattr(core, name, val)
        shutil.rmtree(self.dir, ignore_errors=True)


class _BaseHandler(BaseHTTPRequestHandler):
    server_version = 'SteadyLinkTest/1.0'

    def log_message(self, *args):
        pass


class RangeServer:
    """支持/忽略 Range 的本地文件服务器。

    mode:
      range   —— 正确返回 206 + Content-Range
      norange —— 永远 200 全文（模拟不支持 Range）
      badcr   —— 返回 206 但 Content-Range 区间错误
    """

    def __init__(self, data, mode='range', etag_mode=None):
        self.data = bytes(data)
        self.mode = mode
        self.etag_mode = etag_mode
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.httpd.data = self.data
        self.httpd.mode = mode
        self.httpd.etag_mode = etag_mode
        self.httpd.requests = 0
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}/file'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @staticmethod
    def _make_handler():
        class H(_BaseHandler):
            def do_GET(self):
                srv = self.server
                if self.path.split('?')[0] != '/file':
                    self.send_error(404)
                    return
                data = srv.data
                rng = self.headers.get('Range')
                srv.requests += 1
                if srv.etag_mode == 'change':
                    etag = '"v1"' if srv.requests == 1 else '"v2"'
                else:
                    etag = '"v1"'
                if_range = self.headers.get('If-Range')
                if if_range and if_range != etag:
                    # 资源已变化：按规范忽略 Range，返回整个文件
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('ETag', etag)
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if srv.mode == 'norange' or not rng:
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('ETag', etag)
                    self.end_headers()
                    self.wfile.write(data)
                    return
                m = re.match(r'bytes=(\d+)-(\d*)', rng)
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else len(data) - 1
                end = min(end, len(data) - 1)
                chunk = data[start:end + 1]
                self.send_response(206)
                if srv.mode == 'badcr':
                    self.send_header('Content-Range',
                                     f'bytes {start + 5}-{end + 5}/{len(data)}')
                else:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
                self.send_header('Content-Length', str(len(chunk)))
                self.send_header('ETag', etag)
                self.end_headers()
                self.wfile.write(chunk)
        return H


class StaticServer:
    """按路径返回固定内容，用于 HLS 测试。"""

    def __init__(self, routes):
        self.routes = dict(routes)
        outer = self
        class H(_BaseHandler):
            def do_GET(self):
                path = self.path.split('?')[0]
                item = outer.routes.get(path)
                if item is None:
                    self.send_error(404)
                    return
                body, ctype = item
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', ctype)
                self.end_headers()
                self.wfile.write(body)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path='/'):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}{path}'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
