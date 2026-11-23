"""万能多线程下载引擎：直链探测 / Range 分段多线程 / 单流回退 / 网页链接解析。

不依赖第三方库，用系统 curl.exe 发请求，支持通过本地代理端口下载。
"""
import json, os, re, shutil, subprocess, threading, time, urllib.parse
import concurrent.futures as cf

CREATE = 0x08000000
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

SKIP_EXT = {'.html', '.htm', '.php', '.asp', '.aspx', '.jsp', '.js', '.css', '.json', '.xml'}
FILE_EXT = ('.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.exe', '.msi', '.apk', '.dmg', '.iso',
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.epub', '.mobi',
            '.mp4', '.mkv', '.avi', '.mov', '.flv', '.ts', '.m3u8', '.mp3', '.flac', '.wav', '.aac', '.m4a',
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.psd', '.ai', '.rar5', '.torrent',
            '.bin', '.dat', '.py', '.whl', '.jar', '.crx', '.xpi', '.deb', '.rpm')


def _curl(args, timeout=180):
    return subprocess.run(['curl.exe', '-s', '--ssl-no-revoke'] + args, capture_output=True,
                          text=True, timeout=timeout, creationflags=CREATE, errors='replace')


def sanitize(name, maxlen=120):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', (name or '').strip())
    return (name[:maxlen] or 'download')


def filename_from_url(url):
    p = urllib.parse.urlparse(url)
    name = urllib.parse.unquote(os.path.basename(p.path)) or ''
    return sanitize(name) if name and '.' in name else ''


def probe(url, proxy=None, timeout=25):
    """探测文件大小 / 是否支持 Range / 文件名 / 类型。"""
    args = ['-L', '-r', '0-0', '-D', '-', '-o', 'NUL', '--max-time', str(timeout), '-A', UA]
    if proxy:
        args += ['-x', proxy]
    try:
        r = _curl(args + [url], timeout=timeout + 15)
    except Exception as e:
        return {'url': url, 'error': str(e), 'size': None, 'range': False, 'filename': '', 'ctype': ''}
    hdr = r.stdout or ''
    size, rng, ctype, fname = None, False, '', ''
    m = re.search(r'Content-Range:\s*bytes\s+\d+-\d+/(\d+)', hdr, re.I)
    if m:
        size = int(m.group(1))
        rng = True
    else:
        m = re.search(r'Content-Length:\s*(\d+)', hdr, re.I)
        if m:
            size = int(m.group(1))
    m = re.search(r'Content-Type:\s*([^\r\n;]+)', hdr, re.I)
    if m:
        ctype = m.group(1).strip()
    m = re.search(r"Content-Disposition:[^\r\n]*?filename\*?=(?:UTF-8'')?([^;\r\n]+)", hdr, re.I)
    if m:
        fname = sanitize(urllib.parse.unquote(m.group(1).strip().strip('"')))
    if not fname:
        fname = filename_from_url(url) or 'download.bin'
    return {'url': url, 'error': None, 'size': size, 'range': rng, 'ctype': ctype, 'filename': fname}


def human(n):
    try:
        n = float(n or 0)
    except Exception:
        return '?'
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or u == 'TB':
            return (f'{n:.0f}{u}' if u == 'B' else f'{n:.2f}{u}')
        n /= 1024
    return '?'



def _parts_meta_path(out_path):
    return out_path + '.parts.json'


def _load_json_file(path):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return None


def _write_json_file(path, data):
    try:
        tmp = path + '.tmp'
        json.dump(data, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _remove_file(path):
    try:
        os.unlink(path)
    except Exception:
        pass


def _prepare_parts_meta(out_path, part_dir, meta):
    """校验续传分片是否属于同一资源；不属于（或无元数据）就清空重下。返回是否清理过。"""
    old = _load_json_file(_parts_meta_path(out_path))
    stale = False
    if old is None:
        try:
            if os.path.isdir(part_dir) and os.listdir(part_dir):
                stale = True
        except Exception:
            pass
    else:
        for k, v in meta.items():
            if old.get(k) != v:
                stale = True
                break
    if stale:
        shutil.rmtree(part_dir, ignore_errors=True)
    _write_json_file(_parts_meta_path(out_path), meta)
    return stale


def _fetch_chunk(url, part, start, end, proxy, stop, on_bytes, retries=8):
    """下载一个分段；严格校验 206 状态、Content-Range 与长度。
    返回 'ok' / 'norange'（服务器忽略 Range）/ 'error' / 'stopped'。"""
    need = end - start + 1
    if os.path.exists(part) and os.path.getsize(part) > need:
        try:
            os.unlink(part)
        except Exception:
            pass
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have >= need:
        return 'ok'
    attempt = 0
    while have < need and attempt < retries:
        if stop.is_set():
            return 'stopped'
        attempt += 1
        tmp = part + '.tmp'
        hdr = part + '.hdr'
        args = ['-L', '-D', hdr, '-r', f'{start + have}-{end}', '--max-time', '120',
                '-A', UA, '-o', tmp, '-w', '%{http_code}']
        if proxy:
            args += ['-x', proxy]
        try:
            r = _curl(args + [url], timeout=140)
            code = (r.stdout or '').strip()
        except Exception:
            code = ''
        head = ''
        try:
            head = open(hdr, encoding='utf-8', errors='replace').read()
        except Exception:
            pass
        try:
            os.unlink(hdr)
        except Exception:
            pass
        add = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if code == '200':
            try:
                os.unlink(tmp)
            except Exception:
                pass
            return 'norange'
        if code not in ('206',) or add <= 0:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            time.sleep(min(1 + attempt * 0.5, 6))
            continue
        cr = re.findall(r'Content-Range:\s*bytes\s+(\d+)-(\d+)/(\d+|\*)', head, re.I)
        if not cr:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            time.sleep(1)
            continue
        cr_start, cr_end = int(cr[-1][0]), int(cr[-1][1])
        if cr_start != start + have or cr_end != end:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            time.sleep(1)
            continue
        want = need - have
        if add > want:
            add = want
        with open(part, 'ab') as f, open(tmp, 'rb') as g:
            f.write(g.read(add))
        have = os.path.getsize(part)
        on_bytes(add)
        try:
            os.unlink(tmp)
        except Exception:
            pass
        if have < need:
            time.sleep(min(1 + attempt * 0.5, 6))
    return 'ok' if have >= need else 'error'


def download(url, out_path, proxy=None, threads=8, chunk=8 * 1024 * 1024,
             progress=None, stop=None, max_seconds=None):
    """多线程断点续传下载。

    progress(done_bytes, total_bytes, speed_bytes_per_sec) 会周期性回调；
    stop 是 threading.Event，置位后中止并保留 .parts 以便续传。
    """
    stop = stop or threading.Event()
    info = probe(url, proxy)
    if info.get('error'):
        return False, f"探测失败：{info['error']}"
    if info.get('range') is not None and info.get('size') and info['range'] and threads > 1:
        return _download_segments(url, out_path, info['size'], proxy, threads, chunk, progress, stop, max_seconds)
    return _download_single(url, out_path, proxy, progress, stop, max_seconds)


def _download_segments(url, out_path, size, proxy, threads, chunk, progress, stop, max_seconds):
    part_dir = out_path + '.parts'
    meta = {'url': url, 'size': size, 'chunk': chunk, 'mode': 'segments'}
    _prepare_parts_meta(out_path, part_dir, meta)
    os.makedirs(part_dir, exist_ok=True)
    lock = threading.Lock()
    done = [0]
    t0 = time.time()

    # 已存在的部分（续传）
    chunks = []
    idx = 0
    for start in range(0, size, chunk):
        end = min(start + chunk - 1, size - 1)
        chunks.append((idx, start, end))
        p = os.path.join(part_dir, f'{idx:05d}.part')
        if os.path.exists(p):
            with lock:
                done[0] += min(os.path.getsize(p), end - start + 1)
        idx += 1

    def on_bytes(n):
        with lock:
            done[0] += n

    def reporter():
        last = done[0]
        while not stop.is_set():
            time.sleep(0.8)
            with lock:
                cur = done[0]
            sp = (cur - last) / 0.8
            last = cur
            if progress:
                progress(cur, size, sp)
            if max_seconds and time.time() - t0 > max_seconds:
                stop.set()

    rep = threading.Thread(target=reporter, daemon=True)
    rep.start()

    def work(c):
        i, s, e = c
        part = os.path.join(part_dir, f'{i:05d}.part')
        return _fetch_chunk(url, part, s, e, proxy, stop, on_bytes)

    with cf.ThreadPoolExecutor(max_workers=max(1, int(threads))) as ex:
        results = list(ex.map(work, chunks))

    if progress:
        progress(done[0], size, 0)
    if stop.is_set() or 'stopped' in results:
        return False, '已取消（已保留分片，下次可续传）'
    if 'norange' in results:
        # 服务器忽略 Range：放弃分段，清理后回退单流下载
        import shutil as _sh
        _sh.rmtree(part_dir, ignore_errors=True)
        return _download_single(url, out_path, proxy, progress, stop, max_seconds)
    if 'error' in results:
        return False, '分片下载失败（已保留分片，可重试续传）'
    # 严格校验每个分片大小
    for i, st, en in chunks:
        part = os.path.join(part_dir, f'{i:05d}.part')
        if not os.path.exists(part) or os.path.getsize(part) != en - st + 1:
            return False, f'分片校验失败（第 {i + 1} 段大小不符）'

    tmp_out = out_path + '.merging'
    with open(tmp_out, 'wb') as out:
        for i, s, e in chunks:
            part = os.path.join(part_dir, f'{i:05d}.part')
            with open(part, 'rb') as f:
                while True:
                    b = f.read(4 * 1024 * 1024)
                    if not b:
                        break
                    out.write(b)
    if os.path.getsize(tmp_out) != size:
        return False, '合并后大小不一致（可重试续传）'
    os.replace(tmp_out, out_path)
    shutil.rmtree(part_dir, ignore_errors=True)
    _remove_file(_parts_meta_path(out_path))
    return True, out_path


def _download_single(url, out_path, proxy, progress, stop, max_seconds):
    meta = {'url': url, 'mode': 'single'}
    old = _load_json_file(_parts_meta_path(out_path))
    if os.path.exists(out_path) and old != meta:
        _remove_file(out_path)  # 不是同一个资源，不能续传
    if old != meta:
        _write_json_file(_parts_meta_path(out_path), meta)
    resume = os.path.exists(out_path) and os.path.getsize(out_path) > 0
    args = ['curl.exe', '-sS', '--ssl-no-revoke', '-L', '--max-time', '7200', '-A', UA,
            '-o', out_path, '--progress-bar']
    if resume:
        args += ['-C', '-']
    if proxy:
        args += ['-x', proxy]
    args.append(url)
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=CREATE)
    t0 = time.time()
    last = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    while p.poll() is None:
        if stop.is_set():
            try:
                p.kill()
            except Exception:
                pass
            return False, '已取消'
        if max_seconds and time.time() - t0 > max_seconds:
            try:
                p.kill()
            except Exception:
                pass
            return False, '超时中止'
        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        sp = size - last
        last = size
        if progress:
            progress(size, 0, sp)
        time.sleep(1)
    ok = p.returncode == 0 and os.path.exists(out_path)
    if not ok and resume:
        # 服务器不支持续传：删掉半截文件重下一遍
        _remove_file(out_path)
        return _download_single(url, out_path, proxy, progress, stop, max_seconds)
    if ok:
        _remove_file(_parts_meta_path(out_path))
        return True, out_path
    return False, f'失败（curl 退出码 {p.returncode}）'


def parse_page_links(html, base_url, proxy=None):
    """从 HTML 中提取可下载的链接：返回 [(绝对URL, 链接文字, 扩展名)]，去重并按扩展名过滤。"""
    res, seen = [], set()
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = m.group(1).strip()
        text = re.sub(r'<[^>]+>', '', m.group(2)).strip()[:80]
        url = urllib.parse.urljoin(base_url, href)
        u = urllib.parse.urlparse(url)
        if u.scheme not in ('http', 'https'):
            continue
        ext = os.path.splitext(u.path.lower())[1]
        if ext in SKIP_EXT:
            continue
        if not ext and 'download' not in url.lower():
            continue
        if url in seen:
            continue
        seen.add(url)
        res.append((url, text or os.path.basename(u.path), ext))
    for m in re.finditer(r'<(?:video|audio|source|img)\s[^>]*src=["\']([^"\'#]+)["\']', html, re.I):
        url = urllib.parse.urljoin(base_url, m.group(1).strip())
        u = urllib.parse.urlparse(url)
        if u.scheme in ('http', 'https') and url not in seen:
            seen.add(url)
            ext = os.path.splitext(u.path.lower())[1]
            res.append((url, os.path.basename(u.path), ext))
    # 常见直链文件扩展名优先
    res.sort(key=lambda x: (x[2] not in FILE_EXT, x[1]))
    return res


def fetch_page(url, proxy=None, timeout=30):
    args = ['-L', '--max-time', str(timeout), '-A', UA, '--compressed']
    if proxy:
        args += ['-x', proxy]
    try:
        r = _curl(args + [url], timeout=timeout + 15)
        return r.stdout or ''
    except Exception:
        return ''


# ==================== HLS / m3u8 ====================
def is_hls(url):
    return '.m3u8' in (url or '').lower()


def is_magnet(url):
    return (url or '').lower().startswith('magnet:')


def _fetch_bytes(url, proxy=None, timeout=60):
    import tempfile
    fd, p = tempfile.mkstemp()
    os.close(fd)
    args = ['-L', '--max-time', str(timeout), '-A', UA, '-o', p, '-w', '%{http_code}']
    if proxy:
        args += ['-x', proxy]
    try:
        r = _curl(args + [url], timeout=timeout + 15)
        code = (r.stdout or '').strip()
        data = open(p, 'rb').read() if os.path.exists(p) else b''
        return code, data
    finally:
        try:
            os.unlink(p)
        except Exception:
            pass


def parse_m3u8(text, base_url):
    """解析 m3u8：返回 (variants, segments)。segments 元素含 uri/key/seq。"""
    lines = [l.strip() for l in (text or '').splitlines() if l.strip()]
    variants, segs, key = [], [], None
    media_seq = 0
    for i, line in enumerate(lines):
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            try:
                media_seq = int(line.split(':', 1)[1])
            except Exception:
                media_seq = 0
        elif line.startswith('#EXT-X-STREAM-INF'):
            bw = re.search(r'BANDWIDTH=(\d+)', line)
            if i + 1 < len(lines) and not lines[i + 1].startswith('#'):
                variants.append((int(bw.group(1)) if bw else 0,
                                 urllib.parse.urljoin(base_url, lines[i + 1])))
        elif line.startswith('#EXT-X-KEY'):
            m = re.search(r'METHOD=([^,]+)', line)
            method = (m.group(1) if m else 'NONE').strip()
            uri = re.search(r'URI="([^"]+)"', line)
            iv = re.search(r'IV=0x([0-9a-fA-F]+)', line)
            if method not in ('NONE', 'none') and uri:
                key = {'method': method, 'uri': urllib.parse.urljoin(base_url, uri.group(1)),
                       'iv': iv.group(1) if iv else None}
            else:
                key = None
        elif not line.startswith('#'):
            segs.append({'uri': urllib.parse.urljoin(base_url, line), 'key': key,
                         'seq': media_seq + len(segs)})
    return variants, segs


def _aes_decrypt(data, key, iv):
    """AES-128-CBC 解密（PKCS7）。失败抛异常，绝不返回密文冒充明文。"""
    if not data or len(data) % 16 != 0:
        raise ValueError('分片长度不是 16 的倍数，无法解密')
    from Crypto.Cipher import AES
    out = AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    pad = out[-1] if out else 0
    if not 1 <= pad <= 16 or out[-pad:] != bytes([pad]) * pad:
        raise ValueError('AES 解密 padding 校验失败（密钥或 IV 不正确）')
    return out[:-pad]


def _iv_for(seg, key_info):
    if key_info and key_info.get('iv'):
        h = key_info['iv']
        h = h if len(h) == 32 else h.rjust(32, '0')
        return bytes.fromhex(h)
    seq = seg.get('seq', 0)
    return seq.to_bytes(16, 'big')


def hls_download(url, out_path, proxy=None, threads=8, progress=None, stop=None, log=None):
    """下载 m3u8（自动选最高码率子播放列表），分片并发 + AES-128 解密，合并为 .ts。"""
    stop = stop or threading.Event()
    code, data = _fetch_bytes(url, proxy)
    if code not in ('200', '206', '0') or not data:
        return False, f'播放列表获取失败（HTTP {code}）'
    text = data.decode('utf-8', 'replace')
    variants, segs = parse_m3u8(text, url)
    if variants and not segs:
        variants.sort(key=lambda x: -x[0])
        sub_url = variants[0][1]
        if log:
            log(f'HLS: 选择码率 {variants[0][0] // 1000}kbps 的子播放列表')
        code, data = _fetch_bytes(sub_url, proxy)
        if not data:
            return False, '子播放列表获取失败'
        variants, segs = parse_m3u8(data.decode('utf-8', 'replace'), sub_url)
    if not segs:
        return False, '没有解析到 HLS 分片（可能是加密/直播流）'
    total = len(segs)
    bad_methods = {seg['key']['method'].upper() for seg in segs
                   if seg.get('key') and seg['key'].get('method', '').upper() not in ('AES-128',)}
    if bad_methods:
        return False, f'不支持的 HLS 加密方式：{", ".join(sorted(bad_methods))}'
    part_dir = out_path + '.hls_parts'
    hls_meta = {'url': url, 'count': total, 'first': segs[0]['uri'], 'last': segs[-1]['uri'],
                'mode': 'hls'}
    _prepare_parts_meta(out_path, part_dir, hls_meta)
    os.makedirs(part_dir, exist_ok=True)
    done = [0]
    lock = threading.Lock()
    key_cache = {}
    key_lock = threading.Lock()

    def get_key(k):
        if not k:
            return None
        u = k['uri']
        with key_lock:
            if u not in key_cache:
                _, kb = _fetch_bytes(u, proxy, timeout=30)
                key_cache[u] = kb
            return key_cache[u]

    def work(i):
        seg = segs[i]
        part = os.path.join(part_dir, f'{i:06d}.ts')
        if os.path.exists(part) and os.path.getsize(part) > 0:
            with lock:
                done[0] += 1
                if progress:
                    progress(done[0], total, 0)
            return True
        for attempt in range(5):
            if stop.is_set():
                return 'stopped'
            c, b = _fetch_bytes(seg['uri'], proxy, timeout=60)
            if b and c in ('200', '206'):
                if seg.get('key') and seg['key'].get('method', '').upper() == 'AES-128':
                    kb = get_key(seg['key'])
                    if not kb:
                        return 'decrypt_fail'
                    try:
                        b = _aes_decrypt(b, kb, _iv_for(seg, seg['key']))
                    except Exception as e:
                        if log:
                            log(f'HLS 分片解密失败：{e}')
                        return 'decrypt_fail'
                with open(part, 'wb') as f:
                    f.write(b)
                with lock:
                    done[0] += 1
                    if progress:
                        progress(done[0], total, 0)
                return 'ok'
            time.sleep(1 + attempt)
        return 'error' 

    with cf.ThreadPoolExecutor(max_workers=max(1, int(threads))) as ex:
        results = list(ex.map(work, range(total)))
    if stop.is_set() or 'stopped' in results:
        return False, '已取消（分片已保留，可重试续传）'
    if 'decrypt_fail' in results:
        return False, 'HLS 解密失败（密钥/IV 不匹配），未生成文件'
    if 'error' in results:
        return False, '部分分片下载失败（已保留分片，可重试续传）'
    for i in range(total):
        pth = os.path.join(part_dir, f'{i:06d}.ts')
        if not os.path.exists(pth) or os.path.getsize(pth) == 0:
            return False, f'分片完整性校验失败（第 {i + 1} 片缺失）'
    tmp = out_path + '.ts.tmp'
    with open(tmp, 'wb') as out:
        for i in range(total):
            with open(os.path.join(part_dir, f'{i:06d}.ts'), 'rb') as f:
                while True:
                    chunk = f.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    os.replace(tmp, out_path)
    shutil.rmtree(part_dir, ignore_errors=True)
    _remove_file(_parts_meta_path(out_path))
    return True, out_path


# ==================== 磁力 / BT（aria2c） ====================
def aria2_path():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'aria2', 'aria2c.exe')
    return p if os.path.exists(p) else ''


ARIA2_PROGRESS = re.compile(r'\[#\w+\s+([\d.]+)([KMGT]?)iB?/([\d.]+)([KMGT]?)iB?\((\d+)%\).*?DL:([\d.]+)([KMGT]?)iB?')


def _to_bytes(num, unit):
    num = float(num)
    mul = {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}.get(unit, 1)
    return num * mul


def bt_download(magnet, out_dir, proxy=None, progress=None, stop=None, log=None, max_seconds=None):
    """用 aria2c 下载磁力/种子：返回 (ok, msg)。progress(done, total, speed)。"""
    import queue as _queue
    stop = stop or threading.Event()
    aria = aria2_path()
    if not aria:
        return False, '未找到 aria2c（缺少 bin/aria2/aria2c.exe）'
    os.makedirs(out_dir, exist_ok=True)
    cmd = [aria, magnet,
           '--dir', out_dir, '--seed-time=0', '--summary-interval=1',
           '--console-log-level=warn', '--auto-file-renaming=false',
           '--continue=true', '--file-allocation=none',
           '--enable-dht=true', '--dht-listen-port=6881-6999',
           '--bt-tracker-connect-timeout=10', '--bt-stop-timeout=300',
           '--max-connection-per-server=16', '--split=16', '--min-split-size=1M',
           '--no-conf', '--quiet=true']
    if proxy:
        cmd += [f'--all-proxy={proxy}']
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, creationflags=CREATE, errors='replace')
    except Exception as e:
        return False, f'aria2 启动失败: {e}'
    q = _queue.Queue()

    def reader():
        try:
            for line in p.stdout:
                q.put(line)
        except Exception:
            pass
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    t0 = time.time()

    def _abort(reason):
        try:
            p.kill()
        except Exception:
            pass
        return False, reason

    while True:
        if stop.is_set():
            return _abort('已取消')
        if max_seconds and time.time() - t0 > max_seconds:
            return _abort('超时中止')
        try:
            line = q.get(timeout=0.5)
        except _queue.Empty:
            if p.poll() is not None:
                break
            continue
        if line is None:
            break
        line = line.rstrip()
        if not line:
            continue
        m = ARIA2_PROGRESS.search(line)
        if m and progress:
            done = _to_bytes(m.group(1), m.group(2))
            total = _to_bytes(m.group(3), m.group(4))
            speed = _to_bytes(m.group(6), m.group(7))
            progress(done, total, speed)
        elif log and ('[ERROR]' in line or 'error' in line.lower() or '错误' in line):
            log(f'aria2: {line[:200]}')
    rc = p.poll()
    if rc is None:
        try:
            rc = p.wait(timeout=5)
        except Exception:
            rc = -1
    return (True, out_dir) if rc == 0 else (False, f'aria2 退出码 {rc}')


# ==================== 持久化 / 站点规则 / 下载日志 ====================
HERE_DL = os.path.dirname(os.path.abspath(__file__))
TASKS_FILE = os.path.join(HERE_DL, 'dl_tasks.json')
HISTORY_FILE = os.path.join(HERE_DL, 'dl_history.json')
RULES_FILE = os.path.join(HERE_DL, 'site_rules.json')
DL_LOG_FILE = os.path.join(HERE_DL, 'download.log')

DEFAULT_RULES = [
    {'pattern': '*.tsinghua.edu.cn', 'action': 'direct'},
    {'pattern': '*.aliyun.com', 'action': 'direct'},
    {'pattern': '*.aliyuncs.com', 'action': 'direct'},
    {'pattern': '*.mirrors.cloud.tencent.com', 'action': 'direct'},
    {'pattern': '*.huaweicloud.com', 'action': 'direct'},
    {'pattern': '*.baidu.com', 'action': 'direct'},
]


def _load_json(path, default):
    try:
        import json as _json
        return _json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def _save_json(path, data):
    import json as _json
    try:
        tmp = path + '.tmp'
        _json.dump(data, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def load_tasks():
    return _load_json(TASKS_FILE, [])


def save_tasks(tasks):
    _save_json(TASKS_FILE, tasks)


def load_history():
    return _load_json(HISTORY_FILE, [])


def save_history(history):
    _save_json(HISTORY_FILE, history)


def load_rules():
    rules = _load_json(RULES_FILE, None)
    if not isinstance(rules, list) or not rules:
        rules = list(DEFAULT_RULES)
        save_rules(rules)
    return rules


def save_rules(rules):
    _save_json(RULES_FILE, rules)


def rule_action(url, rules):
    """返回 'direct' / 'proxy' / None（未命中）。"""
    import fnmatch
    host = (urllib.parse.urlparse(url).hostname or '').lower()
    for r in rules:
        pat = (r.get('pattern') or '').lower().strip()
        act = r.get('action')
        if not pat or act not in ('direct', 'proxy'):
            continue
        if fnmatch.fnmatch(host, pat) or host.endswith('.' + pat.lstrip('*.')):
            return act
    return None


def dl_log(msg):
    try:
        with open(DL_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass
