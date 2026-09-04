#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AM·Note 应用图标。以 src/icon-master-1024.png 为唯一母版。

用法:
    python3 make_icon.py            产出 icns / _iconset / PWA png（都在 src/）
    python3 make_icon.py --preview  只检查母版，并写出几档预览 png

不重画图形。构建走这条路，不会退回旧的索引卡画法。
缩放优先用 PIL Lanczos；没有 PIL 时退到 macOS 自带的 sips。
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'icon-master-1024.png')
ISET = os.path.join(HERE, '_iconset')

try:
    from PIL import Image, ImageChops
except ImportError:                      # pragma: no cover
    Image = ImageChops = None

ICONSET = [
    ('icon_16x16.png', 16),
    ('icon_16x16@2x.png', 32),
    ('icon_32x32.png', 32),
    ('icon_32x32@2x.png', 64),
    ('icon_128x128.png', 128),
    ('icon_128x128@2x.png', 256),
    ('icon_256x256.png', 256),
    ('icon_256x256@2x.png', 512),
    ('icon_512x512.png', 512),
    ('icon_512x512@2x.png', 1024),
]

PWA = [
    ('icon-192.png', 192),
    ('icon-512.png', 512),
]


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def require_master():
    if not os.path.isfile(MASTER):
        raise SystemExit(
            '找不到图标母版 %s。把 icon-master-1024.png 放到 src/ 再跑。' % MASTER)
    w = h = mode = None
    if Image is not None:
        im = Image.open(MASTER).convert('RGBA')
        w, h = im.size
        mode = im.mode
        px = im.load()
        corners = (px[0, 0][3], px[w - 1, 0][3],
                   px[0, h - 1][3], px[w - 1, h - 1][3])
        if any(a != 0 for a in corners):
            raise SystemExit('母版四角必须是透明，现在 alpha=%s' % (corners,))
    else:
        info = subprocess.check_output(
            ['sips', '-g', 'pixelWidth', '-g', 'pixelHeight',
             '-g', 'hasAlpha', MASTER], text=True)
        for line in info.splitlines():
            if 'pixelWidth:' in line:
                w = int(line.split(':')[1])
            elif 'pixelHeight:' in line:
                h = int(line.split(':')[1])
            elif 'hasAlpha:' in line:
                mode = 'RGBA' if 'yes' in line.lower() else line.strip()
        if 'hasAlpha: yes' not in info:
            raise SystemExit('母版必须带透明通道，当前是：\n' + info)
    if w != 1024 or h != 1024:
        raise SystemExit('母版必须是 1024×1024 PNG，当前是 %sx%s' % (w, h))
    return w, h, mode


def rounded_mask(size, r_frac=0.22, n=3.0, ss=4):
    """目标尺寸上的连续圆角蒙版。小尺寸直接缩 1024 母版时四角会糊成半透明方底。"""
    r = max(1.0, r_frac * size)
    buf = bytearray(size * size)
    inv = 1.0 / ss
    for y in range(size):
        spans = []
        for s in range(ss):
            yy = y + (s + 0.5) / ss
            if yy <= 0.0 or yy >= size:
                continue
            if yy < r:
                t = (r - yy) / r
            elif yy > size - r:
                t = (yy - (size - r)) / r
            else:
                t = 0.0
            if t >= 1.0:
                continue
            if t == 0.0:
                xa, xb = 0.0, float(size)
            else:
                dx = r * (1.0 - t ** n) ** (1.0 / n)
                xa, xb = r - dx, size - r + dx
            if xa < 0.0:
                xa = 0.0
            if xb > size:
                xb = float(size)
            if xb > xa:
                spans.append((xa, xb))
        if not spans:
            continue
        for x in range(size):
            c = 0.0
            for xa, xb in spans:
                lo = xa if xa > x else x
                hi = xb if xb < (x + 1) else (x + 1)
                if hi > lo:
                    c += hi - lo
            v = int(c * inv * 255 + 0.5)
            buf[y * size + x] = 255 if v > 255 else v
    return Image.frombytes('L', (size, size), bytes(buf))


def resize(src, dest, size):
    if size == 1024:
        if os.path.abspath(src) != os.path.abspath(dest):
            shutil.copy2(src, dest)
        return
    if Image is not None:
        im = Image.open(src).convert('RGBA')
        im = im.resize((size, size), Image.Resampling.LANCZOS)
        # 128 及以下：按目标像素重套圆角，避免四角变成半透明方块
        if size <= 128:
            r, g, b, a = im.split()
            a = ImageChops.multiply(a, rounded_mask(size))
            im = Image.merge('RGBA', (r, g, b, a))
        im.save(dest, 'PNG')
        return
    run(['sips', '-z', str(size), str(size), src, '--out', dest])


def write_sizes(pairs, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    written = []
    cache = {}
    for name, size in pairs:
        dest = os.path.join(dest_dir, name)
        if size in cache:
            shutil.copy2(cache[size], dest)
        else:
            resize(MASTER, dest, size)
            cache[size] = dest
        written.append(dest)
    return written


def build_icns():
    if os.path.isdir(ISET):
        shutil.rmtree(ISET)
    write_sizes(ICONSET, ISET)
    tmp = ISET + '.iconset'
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    shutil.copytree(ISET, tmp)
    out = os.path.join(HERE, 'icon.icns')
    try:
        run(['/usr/bin/iconutil', '-c', 'icns', tmp, '-o', out])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv):
    preview_only = '--preview' in argv
    print('母版：%s' % MASTER)
    require_master()
    made = []

    def note(path):
        made.append(path)
        print('  %-40s %8.1f KB' % (os.path.basename(path),
                                    os.path.getsize(path) / 1024.0))

    if preview_only:
        preview = os.path.join(HERE, '_preview')
        os.makedirs(preview, exist_ok=True)
        for size in (16, 32, 64, 128, 512, 1024):
            p = os.path.join(preview, 'icon_%d.png' % size)
            resize(MASTER, p, size)
            note(p)
        print('\n--preview: 预览写在 src/_preview/。')
        return 0

    print('写 _iconset 与 icon.icns')
    note(build_icns())
    print('写 PWA 图标')
    cache = {}
    for name, size in PWA:
        p = os.path.join(HERE, name)
        if size in cache:
            shutil.copy2(cache[size], p)
        else:
            resize(MASTER, p, size)
            cache[size] = p
        note(p)
    print('\n共 %d 个产出文件。' % len(made))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
