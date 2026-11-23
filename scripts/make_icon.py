"""生成 SteadyLink 图标（assets/icon.png + icon.ico）"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets')
os.makedirs(ASSETS, exist_ok=True)
S = 256


def gradient_bg():
    img = Image.new('RGB', (S, S), (30, 64, 175))
    d = ImageDraw.Draw(img)
    top = (37, 99, 235)     # blue-600
    bottom = (15, 23, 42)   # slate-900
    for y in range(S):
        t = y / (S - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (S, y)], fill=c)
    # 圆角遮罩
    mask = Image.new('L', (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([4, 4, S - 5, S - 5], radius=58, fill=255)
    out = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def chain_layer():
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = 16
    # 两个环（45° 旋转后呈链环状）
    d.rounded_rectangle([76, 40, 180, 116], radius=38, outline=(255, 255, 255, 255), width=w)
    d.rounded_rectangle([76, 140, 180, 216], radius=38, outline=(255, 255, 255, 255), width=w)
    return layer.rotate(-45, resample=Image.BICUBIC, center=(S // 2, S // 2))


def build():
    icon = gradient_bg()
    icon = Image.alpha_composite(icon, chain_layer())
    d = ImageDraw.Draw(icon)
    # 右下角绿色状态点（稳定连接）
    cx, cy, r = 190, 190, 30
    d.ellipse([cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4], fill=(255, 255, 255, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(34, 197, 94, 255))
    icon.save(os.path.join(ASSETS, 'icon.png'))
    icon.save(os.path.join(ASSETS, 'icon.ico'),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.resize((64, 64), Image.LANCZOS).save(os.path.join(ASSETS, 'tray.png'))
    print('icons written to', ASSETS)


if __name__ == '__main__':
    build()
