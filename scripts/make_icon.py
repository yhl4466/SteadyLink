"""生成 SteadyLink 图标：assets/icon.png（256）、assets/icon.ico（多尺寸）、tray.png。"""
import os
import struct
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), 'assets')
os.makedirs(ASSETS, exist_ok=True)
S = 256


def _rounded_mask(size, radius):
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _gradient(size):
    img = Image.new('RGB', (size, size))
    d = ImageDraw.Draw(img)
    top = (37, 99, 235)
    bottom = (14, 23, 42)
    for y in range(size):
        t = y / (size - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=c)
    return img


def big_icon():
    img = _gradient(S)
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = 16
    d.rounded_rectangle([76, 40, 180, 116], radius=38, outline=(255, 255, 255, 255), width=w)
    d.rounded_rectangle([76, 140, 180, 216], radius=38, outline=(255, 255, 255, 255), width=w)
    layer = layer.rotate(-45, resample=Image.BICUBIC, center=(S // 2, S // 2))
    img = img.convert('RGBA')
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    cx, cy, r = 190, 190, 30
    d.ellipse([cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4], fill=(255, 255, 255, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(34, 197, 94, 255))
    img.putalpha(_rounded_mask(S, 58))
    return img


def _font(size):
    for name in ('segoeuib.ttf', 'arialbd.ttf', 'msyhbd.ttc'):
        p = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def small_icon(size):
    """小尺寸专用：纯色圆角底 + 简洁标记（<=24 用字母 S，更大用链环）。"""
    scale = 8
    s = size * scale
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=(37, 99, 235, 255))
    if size <= 24:
        # 字母 S：16px 下比链环更清晰
        f = _font(int(s * 0.72))
        bbox = d.textbbox((0, 0), 'S', font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((s - tw) / 2 - bbox[0], (s - th) / 2 - bbox[1]), 'S', font=f,
               fill=(255, 255, 255, 255))
    else:
        lw = max(scale * 2, int(s * 0.09))
        pad = int(s * 0.17)
        d.ellipse([pad, int(s * 0.20), s - pad, int(s * 0.62)], outline='white', width=lw)
        d.ellipse([pad, int(s * 0.38), s - pad, int(s * 0.80)], outline='white', width=lw)
        r = int(s * 0.11)
        cx, cy = int(s * 0.76), int(s * 0.76)
        d.ellipse([cx - r - scale, cy - r - scale, cx + r + scale, cy + r + scale],
                  fill=(255, 255, 255, 255))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(34, 197, 94, 255))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path, images):
    """写多尺寸 ICO（PNG 压缩条目，Vista+ 支持）。"""
    blobs = []
    for size, im in sorted(images.items()):
        buf = BytesIO()
        im.save(buf, format='PNG')
        blobs.append((size, buf.getvalue()))
    header = struct.pack('<HHH', 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    dirs = b''
    for size, data in blobs:
        wh = 0 if size >= 256 else size
        dirs += struct.pack('<BBBBHHII', wh, wh, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, 'wb') as f:
        f.write(header + dirs + b''.join(d for _, d in blobs))


def build():
    big = big_icon()
    big.save(os.path.join(ASSETS, 'icon.png'))
    images = {s: small_icon(s) for s in (16, 20, 24, 32, 40, 48)}
    for s in (64, 128, 256):
        images[s] = big.resize((s, s), Image.LANCZOS)
    save_ico(os.path.join(ASSETS, 'icon.ico'), images)
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(ASSETS, 'tray.png'))
    small_icon(32).save(os.path.join(ASSETS, 'icon32.png'))
    print('icons written to', ASSETS)


if __name__ == '__main__':
    build()
