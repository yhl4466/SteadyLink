"""Download Xray-core and aria2 into ./bin (Windows x64)."""
import os, sys, zipfile, tempfile, shutil, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, 'bin')
MIRROR = 'https://gh-proxy.com/'

TARGETS = [
    {
        'name': 'Xray-core',
        'url': 'https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip',
        'target': os.path.join(BIN, 'xray.exe'),
        'member': 'xray.exe',
    },
    {
        'name': 'aria2c',
        'url': 'https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip',
        'target': os.path.join(BIN, 'aria2', 'aria2c.exe'),
        'member': 'aria2c.exe',
    },
]


def download(url, dest):
    for u in (url, MIRROR + url):
        try:
            print('downloading', u)
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, 'wb') as f:
                shutil.copyfileobj(r, f)
            if os.path.getsize(dest) > 0:
                return True
        except Exception as e:
            print('  failed:', e)
    return False


def main():
    os.makedirs(BIN, exist_ok=True)
    for t in TARGETS:
        if os.path.exists(t['target']):
            print('skip (exists):', t['target'])
            continue
        tmp = tempfile.mkdtemp()
        z = os.path.join(tmp, 'pkg.zip')
        if not download(t['url'], z):
            print('!! failed to download', t['name'])
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                for m in zf.namelist():
                    if os.path.basename(m).lower() == t['member'].lower():
                        os.makedirs(os.path.dirname(t['target']), exist_ok=True)
                        with zf.open(m) as src, open(t['target'], 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                        print('ok:', t['target'])
                        break
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print('done.')


if __name__ == '__main__':
    main()
