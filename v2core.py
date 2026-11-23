"""迷你 v2rayN 的核心模块：节点解析、Xray 配置、核心启停、系统代理、测速、用量统计。"""
import base64, ctypes, json, os, re, socket, subprocess, sys, threading, time, urllib.parse, urllib.request, winreg
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, 'bin', 'xray.exe')
CFG_DIR = os.path.join(HERE, 'runtime')
NODES_FILE = os.path.join(HERE, 'nodes.json')
CONFIG_FILE = os.path.join(HERE, 'config.json')
USAGE_FILE = os.path.join(HERE, '用量统计.json')
HISTORY_FILE = os.path.join(HERE, '用量历史.csv')
KEY = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
CREATE = 0x08000000
T1 = 'http://www.gstatic.com/generate_204'
T2 = 'http://cp.cloudflare.com/generate_204'
TRACE = 'https://www.cloudflare.com/cdn-cgi/trace'

DEFAULT_CONFIG = {
    'http_port': 10901,
    'socks_port': 10902,
    'stats_port': 10085,
    'auto_failover': False,
    'subscription': '',
    'auto_update_hours': 0,
    'auto_sort_after_test': True,
    'last_sub_update': 0,
    'tz_follow': True,
    'tz_restore': 'China Standard Time',
    'auto_ip_check': True,
    'core_log_level': 'info',
    'access_log': True,
    'allow_insecure': False,
    'auto_reconnect': True,
}

os.makedirs(CFG_DIR, exist_ok=True)


# ---------------- 基础工具 ----------------
def load_json(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def save_json(path, data):
    try:
        tmp = path + '.tmp'
        json.dump(data, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def load_config():
    c = dict(DEFAULT_CONFIG)
    c.update(load_json(CONFIG_FILE, {}))
    return c


def save_config(c):
    save_json(CONFIG_FILE, c)


def load_nodes():
    return load_json(NODES_FILE, [])


def save_nodes(nodes):
    save_json(NODES_FILE, nodes)


def fmt_bytes(n):
    n = float(n or 0)
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or u == 'TB':
            return f"{n:.0f}B" if u == 'B' else f"{n:.2f}{u}"
        n /= 1024


def alloc_ports(k, start=0):
    socks, ports = [], []
    attempts = 0
    while len(ports) < k and attempts < k * 20 + 50:
        attempts += 1
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', 0))
        except OSError:
            s.close()
            continue
        p = s.getsockname()[1]
        if p in ports:
            s.close()
            continue
        socks.append(s)
        ports.append(p)
    for s in socks:
        s.close()
    return ports


def port_free(port):
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def curl(args, timeout=180):
    return subprocess.run(['curl.exe', '-s'] + args, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE)


# ---------------- 节点解析 ----------------
def _b64d(s):
    s = (s or '').strip().replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    try:
        return base64.b64decode(s).decode('utf-8', 'replace')
    except Exception:
        return ''


def parse_ss(link):
    """解析 Shadowsocks 链接，兼容 SIP002 与旧式整串 base64。"""
    u = urllib.parse.urlparse(link)
    frag = urllib.parse.unquote(u.fragment)
    host, port = u.hostname, u.port
    userinfo = u.username
    method = pw = ''
    if userinfo and host and port:
        ui = urllib.parse.unquote(userinfo)
        pw_url = u.password
        if pw_url is not None:
            # SIP002：ss://method:password@host:port（密码可能被 URL 编码）
            method = ui.split(':', 1)[0]
            pw = urllib.parse.unquote(pw_url)
        elif ':' in ui:
            method, _, pw = ui.partition(':')
        else:
            dec = _b64d(ui)
            method, _, pw = dec.partition(':')
    else:
        raw = link[5:].split('#')[0].split('?')[0].strip()
        dec = _b64d(raw)
        m = re.match(r'^([^:]+):(.*)@(.+):(\d+)$', dec, re.S)
        if not m:
            return None
        method, pw, host, port = m.group(1), m.group(2), m.group(3), int(m.group(4))
    if not method or not pw or not host or not port:
        return None
    return {'link': link, 'ct': 3, 'addr': host, 'port': port, 'uuid': pw,
            'security': method, 'net': 'tcp', 'sec': 'none', 'sni': '', 'fp': '', 'pbk': '',
            'sid': '', 'spx': '', 'flow': '', 'host': '', 'path': '', 'alpn': '',
            'allow': False,
            'remark': frag or f'{host}:{port}', 'proto': 'ss'}


def parse_node(link):
    link = (link or '').strip()
    if link.startswith('vless://'):
        u = urllib.parse.urlparse(link)
        q = urllib.parse.parse_qs(u.query)

        def g(k, d=''):
            return (q.get(k) or [d])[0]

        return {'link': link, 'ct': 5, 'addr': u.hostname, 'port': u.port, 'uuid': u.username,
                'net': g('type', 'tcp') or 'tcp', 'sec': g('security', 'none'),
                'sni': g('sni') or g('servername'), 'fp': g('fp'), 'pbk': g('pbk'),
                'sid': g('sid'), 'spx': g('spx'), 'flow': g('flow'),
                'host': g('host'), 'path': g('path'), 'alpn': g('alpn'),
                'allow': g('allowInsecure') in ('1', 'true'),
                'remark': urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}",
                'proto': 'vless'}
    if link.startswith('vmess://'):
        raw = link[8:].split('#')[0].strip()
        raw += '=' * (-len(raw) % 4)
        o = json.loads(base64.b64decode(raw).decode('utf-8', 'replace'))
        return {'link': link, 'ct': 1, 'addr': o.get('add'), 'port': int(o.get('port', 0) or 0),
                'uuid': o.get('id'), 'net': o.get('net', 'tcp'), 'sec': 'tls' if o.get('tls') == 'tls' else 'none',
                'sni': o.get('sni', ''), 'fp': o.get('fp', ''), 'pbk': '', 'sid': '', 'spx': '',
                'flow': '', 'host': o.get('host', ''), 'path': o.get('path', ''),
                'alpn': o.get('alpn', ''), 'allow': False, 'security': o.get('scy', 'auto'),
                'remark': o.get('ps') or f"{o.get('add')}:{o.get('port')}", 'proto': 'vmess'}
    if link.startswith('trojan://'):
        u = urllib.parse.urlparse(link)
        q = urllib.parse.parse_qs(u.query)

        def g(k, d=''):
            return (q.get(k) or [d])[0]

        return {'link': link, 'ct': 6, 'addr': u.hostname, 'port': u.port,
                'uuid': urllib.parse.unquote(u.username or ''), 'net': g('type', 'tcp'),
                'sec': 'tls', 'sni': g('sni'), 'fp': g('fp'), 'pbk': '', 'sid': '', 'spx': '',
                'flow': '', 'host': g('host'), 'path': g('path'), 'alpn': g('alpn'),
                'allow': g('allowInsecure') in ('1', 'true', 'yes'),
                'remark': urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}",
                'proto': 'trojan'}
    if link.startswith('ss://'):
        return parse_ss(link)
    return None


def valid_node(n):
    import re
    if not n or not n.get('addr') or not n.get('port'):
        return False
    if not (0 < n['port'] <= 65535):
        return False
    if n['ct'] in (1, 5) and not re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
                                          str(n.get('uuid') or '')):
        return False
    return True


def stream_settings(n, allow_insecure=False):
    ss = {'network': n['net']}
    if n['net'] == 'ws':
        ss['wsSettings'] = {'path': n.get('path') or '/',
                            'headers': {'Host': n['host']} if n.get('host') else {}}
    elif n['net'] == 'grpc':
        ss['grpcSettings'] = {'serviceName': n.get('path') or ''}
    elif n['net'] == 'h2':
        ss['httpSettings'] = {'path': n.get('path') or '/', 'host': [n['host']] if n.get('host') else []}
    if n.get('sec') == 'reality':
        ss['security'] = 'reality'
        rs = {'serverName': n['sni'] or n.get('host') or n['addr'], 'fingerprint': n['fp'] or 'chrome'}
        if n.get('pbk'):
            rs['publicKey'] = n['pbk']
        if n.get('sid'):
            rs['shortId'] = n['sid']
        if n.get('spx'):
            rs['spiderX'] = n['spx']
        ss['realitySettings'] = rs
    elif n.get('sec') == 'tls':
        ss['security'] = 'tls'
        ts = {'serverName': n['sni'] or n.get('host') or n['addr'],
              'allowInsecure': bool(n.get('allow') or allow_insecure)}
        if n.get('alpn'):
            ts['alpn'] = [a for a in n['alpn'].replace(',', ' ').split() if a]
        if n.get('fp'):
            ts['fingerprint'] = n['fp']
        ss['tlsSettings'] = ts
    else:
        ss['security'] = 'none'
    return ss


def outbound(n, tag='proxy', allow_insecure=False):
    o = {'protocol': '', 'settings': {}, 'streamSettings': stream_settings(n, allow_insecure), 'tag': tag}
    if n['ct'] == 5:
        o['protocol'] = 'vless'
        user = {'id': n['uuid'], 'encryption': 'none'}
        if n.get('flow'):
            user['flow'] = n['flow']
        o['settings'] = {'vnext': [{'address': n['addr'], 'port': n['port'], 'users': [user]}]}
    elif n['ct'] == 1:
        o['protocol'] = 'vmess'
        o['settings'] = {'vnext': [{'address': n['addr'], 'port': n['port'], 'users': [
            {'id': n['uuid'], 'alterId': 0, 'security': n.get('security') or 'auto'}]}]}
    elif n['ct'] == 6:
        o['protocol'] = 'trojan'
        o['settings'] = {'servers': [{'address': n['addr'], 'port': n['port'], 'password': n['uuid']}]}
    elif n['ct'] == 3:
        o['protocol'] = 'shadowsocks'
        o['settings'] = {'servers': [{'address': n['addr'], 'port': n['port'],
                                      'method': n.get('security'), 'password': n['uuid']}]}
    else:
        return None
    return o


def build_config(n, http_port, socks_port, stats_port, log_level='warning', access_log=None,
                 allow_insecure=False):
    ob = outbound(n, 'proxy', allow_insecure)
    if not ob:
        return None
    log_cfg = {'loglevel': log_level or 'warning', 'timestamp': True}
    if access_log:
        try:
            open(access_log, 'w', encoding='utf-8').close()  # 会话开始清空
        except Exception:
            pass
        log_cfg['access'] = access_log
    return {
        'log': log_cfg,
        'stats': {},
        'policy': {'levels': {'0': {'statsUserUplink': True, 'statsUserDownlink': True}},
                   'system': {'statsInboundUplink': True, 'statsInboundDownlink': True,
                              'statsOutboundUplink': True, 'statsOutboundDownlink': True}},
        'api': {'tag': 'api', 'services': ['StatsService']},
        'inbounds': [
            {'tag': 'http-in', 'listen': '127.0.0.1', 'port': http_port, 'protocol': 'http'},
            {'tag': 'socks-in', 'listen': '127.0.0.1', 'port': socks_port, 'protocol': 'socks',
             'settings': {'udp': True}},
            {'tag': 'api', 'listen': '127.0.0.1', 'port': stats_port, 'protocol': 'dokodemo-door',
             'settings': {'address': '127.0.0.1'}},
        ],
        'outbounds': [ob, {'protocol': 'blackhole', 'tag': 'api'}],
        'routing': {'rules': [{'type': 'field', 'inboundTag': ['api'], 'outboundTag': 'api'}]},
    }


# ---------------- 核心管理 ----------------
class Core:
    def __init__(self):
        self.proc = None
        self.cfg_path = os.path.join(CFG_DIR, 'active.json')
        self.on_line = None
        self._reader = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, node, cfg, on_line=None):
        self.stop()
        self.on_line = on_line
        access_path = os.path.join(CFG_DIR, 'access.log') if cfg.get('access_log') else None
        c = build_config(node, cfg['http_port'], cfg['socks_port'], cfg['stats_port'],
                         log_level=cfg.get('core_log_level', 'info'), access_log=access_path,
                         allow_insecure=bool(cfg.get('allow_insecure')))
        if not c:
            return False
        json.dump(c, open(self.cfg_path, 'w', encoding='utf-8'), ensure_ascii=False)
        try:
            self.proc = subprocess.Popen([CORE, 'run', '-c', self.cfg_path],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, bufsize=1, creationflags=CREATE,
                                         errors='replace')
        except Exception as e:
            self.proc = None
            self.on_line = None
            try:
                if self.on_line:
                    self.on_line(f'核心启动异常：{e}')
            except Exception:
                pass
            return False
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.stop()
                return False
            try:
                with socket.create_connection(('127.0.0.1', cfg['http_port']), timeout=0.3):
                    return True
            except OSError:
                time.sleep(0.05)
        # 启动超时：确保不留残余进程
        self.stop()
        return False

    def _read_stdout(self):
        proc = self.proc
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                line = (line or '').rstrip()
                if line and self.on_line:
                    self.on_line(line)
        except Exception:
            pass

    def stop(self):
        p, self.proc = self.proc, None
        self.on_line = None
        if p:
            try:
                p.kill()
            except Exception:
                pass
            try:
                p.wait(timeout=3)
            except Exception:
                pass
            try:
                if p.stdout:
                    p.stdout.close()
            except Exception:
                pass
        self._reader = None

# ---------------- 系统代理 ----------------
PROXY_BACKUP = os.path.join(CFG_DIR, 'proxy_backup.json')
PROXY_NAMES = ('ProxyEnable', 'ProxyServer', 'ProxyOverride', 'AutoConfigURL')


def _proxy_refresh():
    try:
        w = ctypes.windll.wininet
        w.InternetSetOptionW(0, 39, 0, 0)
        w.InternetSetOptionW(0, 37, 0, 0)
    except Exception:
        pass


def read_proxy_registry():
    """读取当前系统代理完整配置（值 + 类型，缺失为 None）。"""
    data = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ) as k:
            for name in PROXY_NAMES:
                try:
                    v, t = winreg.QueryValueEx(k, name)
                    data[name] = {'value': v, 'type': t}
                except FileNotFoundError:
                    data[name] = None
    except Exception:
        return {}
    return data


def _write_proxy_registry(data):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_ALL_ACCESS) as k:
        for name in PROXY_NAMES:
            item = data.get(name)
            if item is None:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(k, name, 0, item.get('type') or winreg.REG_SZ, item.get('value'))
    _proxy_refresh()


def save_proxy_state(force=False):
    """连接前保存原始系统代理（只在没有备份时保存）。"""
    if os.path.exists(PROXY_BACKUP) and not force:
        return False
    data = read_proxy_registry()
    if not data:
        return False
    try:
        tmp = PROXY_BACKUP + '.tmp'
        json.dump(data, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
        os.replace(tmp, PROXY_BACKUP)
        return True
    except Exception:
        return False


def has_proxy_backup():
    return os.path.exists(PROXY_BACKUP)


def _disable_proxy():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, 'ProxyEnable', 0, winreg.REG_DWORD, 0)
        _proxy_refresh()
        return True
    except Exception:
        return False


def restore_proxy_state():
    """恢复连接前的系统代理；没有备份则单纯关闭。"""
    if not os.path.exists(PROXY_BACKUP):
        return _disable_proxy()
    try:
        data = json.load(open(PROXY_BACKUP, encoding='utf-8'))
    except Exception:
        data = None
    if not data:
        _disable_proxy()
    else:
        try:
            _write_proxy_registry(data)
        except Exception:
            return False
    try:
        os.unlink(PROXY_BACKUP)
    except Exception:
        pass
    return True


def set_system_proxy(port):
    if port is None:
        return restore_proxy_state()
    # 备份失败则拒绝接管系统代理，避免退出后无法还原
    saved = save_proxy_state()
    if not saved and not os.path.exists(PROXY_BACKUP):
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, 'ProxyServer', 0, winreg.REG_SZ, f'127.0.0.1:{port}')
            winreg.SetValueEx(k, 'ProxyEnable', 0, winreg.REG_DWORD, 1)
            try:
                winreg.DeleteValue(k, 'AutoConfigURL')
            except FileNotFoundError:
                pass
        _proxy_refresh()
        return True
    except Exception:
        # 写入失败时按备份回滚
        try:
            restore_proxy_state()
        except Exception:
            pass
        return False


def recover_proxy_if_needed(our_ports=()):
    """上次异常退出留下的代理设置：如果指向本程序端口，恢复原状态。"""
    if not has_proxy_backup():
        return False
    try:
        st = read_proxy_registry()
        enabled = (st.get('ProxyEnable') or {}).get('value')
        server = str((st.get('ProxyServer') or {}).get('value') or '')
        if enabled and any(f':{p}' in server for p in our_ports):
            return restore_proxy_state()
    except Exception:
        pass
    return False


def system_proxy_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, 'ProxyEnable')[0] == 1
    except Exception:
        return False


# ---------------- 健康检查 / 测速 ----------------
def health(http_port):
    px = f'http://127.0.0.1:{http_port}'
    r = curl(['-o', 'NUL', '-w', '%{http_code}|%{time_total}', '--max-time', '8', '-x', px, T1], timeout=12)
    code, _, t = (r.stdout or '').partition('|')
    if code.strip() not in ('200', '204'):
        return False, '', '', None
    lat = round(float(t) * 1000) if t.strip() else None
    body = curl(['--max-time', '10', '-x', px, TRACE], timeout=14).stdout or ''
    ip = loc = ''
    for line in body.splitlines():
        if line.startswith('ip='):
            ip = line[3:].strip()
        elif line.startswith('loc='):
            loc = line[4:].strip()
    return True, ip, loc, lat


def _probe(port, url, timeout):
    px = f'http://127.0.0.1:{port}'
    op = urllib.request.build_opener(urllib.request.ProxyHandler({'http': px, 'https': px}))
    t0 = time.time()
    try:
        r = op.open(url, timeout=timeout)
        r.read(5)
        if r.status in (200, 204):
            return round((time.time() - t0) * 1000), None
        return None, f'http{r.status}'
    except Exception as e:
        return None, type(e).__name__


def _run_batch(nodes):
    m = len(nodes)
    ports = alloc_ports(m)
    if len(ports) < m:
        return None
    inbounds, outbounds, rules = [], [], []
    for i, n in enumerate(nodes):
        inbounds.append({'tag': f'in{i}', 'listen': '127.0.0.1', 'port': ports[i], 'protocol': 'http'})
        ob = outbound(n, f'out{i}')
        if not ob:
            return None
        outbounds.append(ob)
        rules.append({'type': 'field', 'inboundTag': [f'in{i}'], 'outboundTag': f'out{i}'})
    cfg = {'log': {'loglevel': 'none'}, 'inbounds': inbounds, 'outbounds': outbounds,
           'routing': {'rules': rules}}
    p = os.path.join(CFG_DIR, f'test_{os.getpid()}_{threading.get_ident()}.json')
    json.dump(cfg, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    proc = subprocess.Popen([CORE, 'run', '-c', p], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=CREATE)
    try:
        deadline = time.time() + (2.5 + m * 0.02)
        pending = set(ports)
        while pending and time.time() < deadline:
            for pt in list(pending):
                try:
                    with socket.create_connection(('127.0.0.1', pt), timeout=0.15):
                        pending.discard(pt)
                except OSError:
                    pass
            if pending:
                time.sleep(0.03)
        if len(pending) > max(2, m // 4) or proc.poll() is not None:
            return None

        def one(i):
            lat, e1 = _probe(ports[i], T1, 3.0)
            if lat is not None:
                return {'ok': True, 'lat': lat, 'err': None}
            lat2, e2 = _probe(ports[i], T2, 2.0)
            if lat2 is not None:
                return {'ok': True, 'lat': lat2, 'err': None}
            return {'ok': False, 'lat': None, 'err': f'{e1}/{e2}'}

        with cf.ThreadPoolExecutor(max_workers=min(200, m)) as ex:
            return list(ex.map(one, range(m)))
    finally:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
        try:
            os.unlink(p)
        except Exception:
            pass


def test_batch(nodes):
    r = _run_batch(nodes)
    if r is not None:
        return r
    if len(nodes) <= 1:
        return [{'ok': False, 'lat': None, 'err': 'core_start_fail'}]
    mid = len(nodes) // 2
    return test_batch(nodes[:mid]) + test_batch(nodes[mid:])


def test_nodes(nodes, progress=None):
    """并发测速：返回 {index: result}。"""
    results = {}
    lock = threading.Lock()
    chunks = [list(range(i, min(i + 100, len(nodes)))) for i in range(0, len(nodes), 100)]

    def work(ci):
        idx = chunks[ci]
        r = test_batch([nodes[i] for i in idx])
        with lock:
            for i, x in zip(idx, r):
                results[i] = x
            if progress:
                progress(len(results), len(nodes))

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(work, range(len(chunks))))
    return results


# ---------------- 订阅 ----------------
def fetch_subscription(url, proxy_port=None):
    path = os.path.join(CFG_DIR, 'sub.tmp')
    args = ['-L', '--max-time', '30', '-A', 'v2rayN/7.25.5', '-o', path,
            '-w', '%{http_code}']
    if proxy_port:
        args += ['-x', f'http://127.0.0.1:{proxy_port}']
    args.append(url)
    r = curl(args, timeout=45)
    code = (r.stdout or '').strip()
    raw = b''
    if os.path.exists(path):
        try:
            raw = open(path, 'rb').read()
        except Exception:
            raw = b''
        try:
            os.unlink(path)
        except Exception:
            pass
    if not raw:
        if code == '204':
            return None, ('订阅返回 HTTP 204 空内容。常见原因：订阅链接已失效/被机场重置、'
                          '链接被限流、或机场要求特定客户端。请回机场面板重新复制订阅链接。')
        if code and code != '000':
            return None, f'订阅下载失败：HTTP {code}'
        return None, '订阅下载失败（网络不通或链接错误）'
    return raw, None


def decode_links(raw):
    txt = raw.decode('utf-8', 'replace').strip()
    if '://' not in txt:
        try:
            pad = '=' * (-len(txt.replace('\n', '')) % 4)
            txt = base64.b64decode(txt.replace('\n', '').replace('\r', '') + pad).decode('utf-8', 'replace')
        except Exception:
            return []
    return [l.strip() for l in txt.splitlines() if '://' in l]


# ---------------- 用量统计 ----------------
class Usage:
    def __init__(self):
        self.committed = self._load()
        self.base = [0, 0]
        self.last = [0, 0]

    def _load(self):
        today = time.strftime('%Y-%m-%d')
        d = load_json(USAGE_FILE, {})
        d.setdefault('date', today)
        for k in ('today_up', 'today_down', 'total_up', 'total_down', 'sessions'):
            d.setdefault(k, 0)
        return d

    def _archive(self):
        today = time.strftime('%Y-%m-%d')
        d = self.committed
        if d.get('date') != today:
            try:
                with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"{d['date']},{d['today_up']},{d['today_down']}\n")
            except Exception:
                pass
            d['date'] = today
            d['today_up'] = d['today_down'] = 0

    def poll(self, cfg):
        try:
            r = subprocess.run([CORE, 'api', 'statsquery', '--server=127.0.0.1:%d' % cfg['stats_port'],
                                '-pattern', 'outbound>>>proxy>>>'],
                               capture_output=True, text=True, timeout=12, creationflags=CREATE)
            data = json.loads(r.stdout or '{}')
            up = down = 0
            for item in data.get('stat', []):
                v = int(item.get('value') or 0)
                if 'uplink' in item.get('name', ''):
                    up = v
                elif 'downlink' in item.get('name', ''):
                    down = v
            self.last = [up, down]
            return up, down
        except Exception:
            return None

    def roll(self):
        self.base[0] += self.last[0]
        self.base[1] += self.last[1]
        self.last = [0, 0]

    def live(self):
        return self.base[0] + self.last[0], self.base[1] + self.last[1]

    def snapshot(self):
        self._archive()
        up, down = self.live()
        d = dict(self.committed)
        d['today_up'] += up
        d['today_down'] += down
        d['total_up'] += up
        d['total_down'] += down
        d['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        save_json(USAGE_FILE, d)
        return d

    def commit(self):
        self._archive()
        up, down = self.live()
        d = dict(self.committed)
        d['today_up'] += up
        d['today_down'] += down
        d['total_up'] += up
        d['total_down'] += down
        d['sessions'] += 1
        d['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        save_json(USAGE_FILE, d)
        self.committed = {k: d[k] for k in ('date', 'today_up', 'today_down', 'total_up', 'total_down', 'sessions')}
        self.base = [0, 0]
        self.last = [0, 0]
        return d


def kill_stale():
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='xray.exe'\" | "
          "Where-Object { $_.CommandLine -match 'runtime\\\\|runtime/' } | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True,
                           timeout=20, creationflags=CREATE)
        import re
        for line in (r.stdout or '').splitlines():
            if '|' not in line:
                continue
            pid, cmd = line.split('|', 1)
            m = re.search(r'runtime[\\/]([^\s\"]+\.json)', cmd)
            if not m:
                continue
            f = os.path.join(CFG_DIR, m.group(1))
            try:
                if os.path.exists(f) and time.time() - os.path.getmtime(f) < 300:
                    continue
            except Exception:
                pass
            subprocess.run(['powershell', '-NoProfile', '-Command',
                            f'Stop-Process -Id {pid.strip()} -Force -ErrorAction SilentlyContinue'],
                           capture_output=True, timeout=15, creationflags=CREATE)
    except Exception:
        pass

# ---------------- 时区 ----------------
IANA_TO_WIN = {
    'Asia/Tokyo': 'Tokyo Standard Time', 'Asia/Seoul': 'Korea Standard Time',
    'Asia/Shanghai': 'China Standard Time', 'Asia/Hong_Kong': 'China Standard Time',
    'Asia/Taipei': 'Taipei Standard Time', 'Asia/Singapore': 'Singapore Standard Time',
    'Asia/Kuala_Lumpur': 'Singapore Standard Time', 'Asia/Bangkok': 'SE Asia Standard Time',
    'Asia/Manila': 'Singapore Standard Time', 'Asia/Jakarta': 'SE Asia Standard Time',
    'Asia/Ho_Chi_Minh': 'SE Asia Standard Time', 'Asia/Kolkata': 'India Standard Time',
    'Asia/Dubai': 'Arabian Standard Time', 'Asia/Kathmandu': 'Nepal Standard Time',
    'Europe/London': 'GMT Standard Time', 'Europe/Dublin': 'GMT Standard Time',
    'Europe/Lisbon': 'GMT Standard Time', 'Europe/Berlin': 'W. Europe Standard Time',
    'Europe/Amsterdam': 'W. Europe Standard Time', 'Europe/Zurich': 'W. Europe Standard Time',
    'Europe/Rome': 'W. Europe Standard Time', 'Europe/Paris': 'Romance Standard Time',
    'Europe/Madrid': 'Romance Standard Time', 'Europe/Warsaw': 'Central European Standard Time',
    'Europe/Moscow': 'Russian Standard Time', 'Europe/Stockholm': 'W. Europe Standard Time',
    'America/New_York': 'Eastern Standard Time', 'America/Toronto': 'Eastern Standard Time',
    'America/Chicago': 'Central Standard Time', 'America/Denver': 'Mountain Standard Time',
    'America/Phoenix': 'US Mountain Standard Time', 'America/Los_Angeles': 'Pacific Standard Time',
    'America/Vancouver': 'Pacific Standard Time', 'America/Sao_Paulo': 'E. South America Standard Time',
    'Australia/Sydney': 'AUS Eastern Standard Time', 'Australia/Melbourne': 'AUS Eastern Standard Time',
    'Pacific/Auckland': 'New Zealand Standard Time', 'UTC': 'UTC', 'Etc/UTC': 'UTC',
}

COUNTRY_TO_WIN = {
    'JP': 'Tokyo Standard Time', 'KR': 'Korea Standard Time', 'SG': 'Singapore Standard Time',
    'MY': 'Singapore Standard Time', 'TW': 'Taipei Standard Time', 'HK': 'China Standard Time',
    'CN': 'China Standard Time', 'US': 'Pacific Standard Time', 'CA': 'Eastern Standard Time',
    'GB': 'GMT Standard Time', 'DE': 'W. Europe Standard Time', 'NL': 'W. Europe Standard Time',
    'CH': 'W. Europe Standard Time', 'FR': 'Romance Standard Time', 'RU': 'Russian Standard Time',
    'AU': 'AUS Eastern Standard Time', 'NZ': 'New Zealand Standard Time',
    'IN': 'India Standard Time', 'TH': 'SE Asia Standard Time', 'VN': 'SE Asia Standard Time',
    'ID': 'SE Asia Standard Time', 'PH': 'Singapore Standard Time', 'AE': 'Arabian Standard Time',
}

TZ_LABEL = {
    'Tokyo Standard Time': '日本时间 (UTC+9)',
    'Korea Standard Time': '韩国时间 (UTC+9)',
    'China Standard Time': '北京时间 (UTC+8)',
    'Taipei Standard Time': '台北时间 (UTC+8)',
    'Singapore Standard Time': '新加坡时间 (UTC+8)',
    'SE Asia Standard Time': '东南亚时间 (UTC+7)',
    'India Standard Time': '印度时间 (UTC+5:30)',
    'Nepal Standard Time': '尼泊尔时间 (UTC+5:45)',
    'Arabian Standard Time': '迪拜时间 (UTC+4)',
    'GMT Standard Time': '英国时间 (UTC+0/+1)',
    'W. Europe Standard Time': '中欧时间 (UTC+1/+2)',
    'Romance Standard Time': '西欧时间 (UTC+1/+2)',
    'Central European Standard Time': '中欧时间 (UTC+1/+2)',
    'Russian Standard Time': '莫斯科时间 (UTC+3)',
    'Eastern Standard Time': '美国东部时间 (UTC-5/-4)',
    'Central Standard Time': '美国中部时间 (UTC-6/-5)',
    'Mountain Standard Time': '美国山地时间 (UTC-7/-6)',
    'US Mountain Standard Time': '亚利桑那时间 (UTC-7)',
    'Pacific Standard Time': '美国太平洋时间 (UTC-8/-7)',
    'AUS Eastern Standard Time': '悉尼时间 (UTC+10/+11)',
    'New Zealand Standard Time': '新西兰时间 (UTC+12/+13)',
    'E. South America Standard Time': '巴西时间 (UTC-3)',
}


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def current_timezone():
    try:
        r = subprocess.run(['tzutil', '/g'], capture_output=True, text=True, timeout=10,
                           creationflags=CREATE)
        return (r.stdout or '').strip()
    except Exception:
        return ''


def tz_label(wid):
    return TZ_LABEL.get(wid or '', wid or '')


def set_timezone(wid):
    if not wid:
        return False, '未知时区'
    try:
        r = subprocess.run(['tzutil', '/s', wid], capture_output=True, text=True, timeout=15,
                           creationflags=CREATE)
        ok = r.returncode == 0
        msg = ((r.stdout or '') + (r.stderr or '')).strip()
        return ok, msg
    except Exception as e:
        return False, str(e)


def geo_timezone(http_port):
    """通过代理查询出口 IP 的地理信息，返回 {'ip','country','iana','win'}；失败返回 None。"""
    body = ''
    for url in ('http://ip-api.com/json/?fields=status,countryCode,timezone,query',
                'http://ip-api.com/json/'):
        try:
            r = curl(['--max-time', '8', '-x', f'http://127.0.0.1:{http_port}', url], timeout=12)
            body = r.stdout or ''
            if 'timezone' in body or 'countryCode' in body:
                break
        except Exception:
            body = ''
    try:
        d = json.loads(body)
    except Exception:
        return None
    if d.get('status') not in (None, 'success'):
        return None
    iana = d.get('timezone') or ''
    cc = d.get('countryCode') or d.get('country') or ''
    win = IANA_TO_WIN.get(iana) or COUNTRY_TO_WIN.get(cc)
    return {'ip': d.get('query', ''), 'country': cc, 'iana': iana, 'win': win}

# ---------------- IP 体检 ----------------
def ip_health(http_port):
    """通过代理查询出口 IP 的安全画像（是否托管/代理、ASN、时区等）。"""
    url = ('http://ip-api.com/json/?fields=status,query,country,countryCode,regionName,city,'
           'isp,org,as,proxy,hosting,timezone')
    body = ''
    try:
        r = curl(['--max-time', '8', '-x', f'http://127.0.0.1:{http_port}', url], timeout=12)
        body = r.stdout or ''
    except Exception:
        return None
    try:
        d = json.loads(body)
    except Exception:
        return None
    if d.get('status') != 'success':
        return None
    return d


def ip_health_verdict(d, node_win_tz=''):
    """把 ip-api 结果转成 (等级, 问题列表, 建议列表)。等级: good / warn / bad"""
    issues, tips = [], []
    if d.get('proxy'):
        issues.append('该 IP 已被标记为代理/VPN（网站会更容易识别）')
        tips.append('换一个未被标记的节点（优先住宅/原生 IP 节点）')
    if d.get('hosting'):
        issues.append('该 IP 属于机房/云服务商（多数风控会重点观察）')
        tips.append('优先选择机场的「住宅 / 家宽 / 原生 IP」节点')
    if node_win_tz and d.get('timezone'):
        pass  # 时区一致性由 GUI 用节点映射判断
    level = 'bad' if d.get('proxy') else ('warn' if d.get('hosting') else 'good')
    return level, issues, tips
