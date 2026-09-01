#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AM·Note 应用图标生成器。纯标准库,不依赖 PIL / numpy / pyobjc。

设计:墨色连续圆角底板 + 一张浅色索引卡 + 卡片右上角凸出的金色标签页。
分档渲染:>=256 完整版,64-128 中档,<=32 极简版。每档单独调比例,不是一张大图缩下去。

用法:
    python3 make_icon.py            产出全部(icns / _iconset / PWA png，都在 src/)
    python3 make_icon.py --preview  只产出 src/ 里的预览大图与分层图

抗锯齿:每个输出像素行取 4 条子扫描线,x 方向按解析覆盖率积分。
等价于「按 4 倍分辨率画再做 4x4 盒式降采样」,但 x 方向比盒式更准,且不用开 16 倍内存。
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ISET = os.path.join(HERE, '_iconset')
STAMP = '20260822'

SS = 4  # 每个输出像素行的子扫描线数


# --------------------------------------------------------------------------
# 一、PNG 编码(zlib + struct,RGBA8,非隔行)
# --------------------------------------------------------------------------

def _chunk(tag, data):
    return (struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))


def write_png(path, w, h, buf):
    """buf 是长度 w*h*4 的 bytearray,straight alpha。行滤波用 Up(2),首行用 None(0)。"""
    stride = w * 4
    raw = bytearray()
    zero = bytes(stride)
    prev = zero
    for y in range(h):
        cur = bytes(buf[y * stride:(y + 1) * stride])
        if y == 0:
            raw.append(0)
            raw += cur
        elif cur == prev:
            raw.append(2)
            raw += zero
        else:
            raw.append(2)
            raw += bytes((c - p) & 255 for c, p in zip(cur, prev))
        prev = cur
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    out = (b'\x89PNG\r\n\x1a\n'
           + _chunk(b'IHDR', ihdr)
           + _chunk(b'IDAT', zlib.compress(bytes(raw), 9))
           + _chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(out)
    return len(out)


# --------------------------------------------------------------------------
# 二、画布与颜色
# --------------------------------------------------------------------------

class Canvas(object):
    __slots__ = ('w', 'h', 'buf')

    def __init__(self, w, h):
        self.w = int(w)
        self.h = int(h)
        self.buf = bytearray(self.w * self.h * 4)

    def save(self, path):
        return write_png(path, self.w, self.h, self.buf)


def rgb(s):
    s = s.lstrip('#')
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def fill_rect(cv, x0, y0, x1, y1, color):
    x0 = max(0, int(x0)); y0 = max(0, int(y0))
    x1 = min(cv.w, int(x1)); y1 = min(cv.h, int(y1))
    if x1 <= x0 or y1 <= y0:
        return
    row = bytes((color[0], color[1], color[2], 255)) * (x1 - x0)
    for y in range(y0, y1):
        i = (y * cv.w + x0) * 4
        cv.buf[i:i + len(row)] = row


def blit(dst, src, ox, oy):
    """source-over 把 src 贴到 dst 的 (ox, oy)。"""
    for y in range(src.h):
        dy = oy + y
        if dy < 0 or dy >= dst.h:
            continue
        sbase = y * src.w * 4
        dbase = (dy * dst.w + ox) * 4
        for x in range(src.w):
            dx = ox + x
            if dx < 0 or dx >= dst.w:
                continue
            si = sbase + x * 4
            sa = src.buf[si + 3]
            if sa == 0:
                continue
            di = dbase + x * 4
            if sa == 255:
                dst.buf[di:di + 4] = src.buf[si:si + 4]
                continue
            af = sa / 255.0
            da = dst.buf[di + 3] / 255.0 * (1.0 - af)
            oa = af + da
            if oa <= 0:
                continue
            inv = 1.0 / oa
            for k in range(3):
                v = int((src.buf[si + k] * af + dst.buf[di + k] * da) * inv + 0.5)
                dst.buf[di + k] = 255 if v > 255 else (0 if v < 0 else v)
            dst.buf[di + 3] = int(oa * 255 + 0.5)


def nn_scale(src, k):
    """最近邻放大,用于小尺寸自检。"""
    out = Canvas(src.w * k, src.h * k)
    for y in range(src.h):
        srow = src.buf[y * src.w * 4:(y + 1) * src.w * 4]
        big = bytearray()
        for x in range(src.w):
            big += srow[x * 4:x * 4 + 4] * k
        for r in range(k):
            i = ((y * k + r) * out.w) * 4
            out.buf[i:i + len(big)] = big
    return out


# --------------------------------------------------------------------------
# 三、形状:连续圆角矩形(超椭圆角)
# --------------------------------------------------------------------------

class RRect(object):
    """|dx/r|^n + |dy/r|^n = 1 的圆角矩形。n=2 是普通圆角,n=5 是苹果那种连续圆角。"""
    __slots__ = ('x0', 'y0', 'x1', 'y1', 'r', 'n')

    def __init__(self, x0, y0, x1, y1, r, n=5.0):
        self.x0 = float(x0); self.y0 = float(y0)
        self.x1 = float(x1); self.y1 = float(y1)
        lim = min(self.x1 - self.x0, self.y1 - self.y0) * 0.5
        self.r = max(0.0, min(float(r), lim))
        self.n = float(n)

    def bounds(self):
        return (self.y0, self.y1)

    def span(self, y):
        if y <= self.y0 or y >= self.y1:
            return None
        r = self.r
        if r <= 0.0:
            return (self.x0, self.x1)
        if y < self.y0 + r:
            dy = (self.y0 + r) - y
        elif y > self.y1 - r:
            dy = y - (self.y1 - r)
        else:
            return (self.x0, self.x1)
        t = dy / r
        if t >= 1.0:
            return None
        dx = r * (1.0 - t ** self.n) ** (1.0 / self.n)
        return (self.x0 + r - dx, self.x1 - r + dx)


class Shifted(object):
    __slots__ = ('s', 'dx', 'dy')

    def __init__(self, s, dx, dy):
        self.s = s; self.dx = float(dx); self.dy = float(dy)

    def bounds(self):
        a, b = self.s.bounds()
        return (a + self.dy, b + self.dy)

    def span(self, y):
        sp = self.s.span(y - self.dy)
        return None if sp is None else (sp[0] + self.dx, sp[1] + self.dx)


class Scaled(object):
    __slots__ = ('s', 'k')

    def __init__(self, s, k):
        self.s = s; self.k = float(k)

    def bounds(self):
        a, b = self.s.bounds()
        return (a * self.k, b * self.k)

    def span(self, y):
        sp = self.s.span(y / self.k)
        return None if sp is None else (sp[0] * self.k, sp[1] * self.k)


# --------------------------------------------------------------------------
# 四、光栅化:逐行解析覆盖率
# --------------------------------------------------------------------------

def _row_cov(shape, y, w, clip=None):
    """返回 (xlo, cov列表, 满覆盖起, 满覆盖止)。cov[j] 对应像素 xlo+j。"""
    spans = []
    for s in range(SS):
        sp = shape.span(y + (s + 0.5) / SS)
        if sp is None:
            continue
        xa, xb = sp
        if clip is not None:
            cp = clip.span(y + (s + 0.5) / SS)
            if cp is None:
                continue
            if cp[0] > xa:
                xa = cp[0]
            if cp[1] < xb:
                xb = cp[1]
        if xa < 0.0:
            xa = 0.0
        if xb > w:
            xb = float(w)
        if xb <= xa:
            continue
        spans.append((xa, xb))
    if not spans:
        return None
    xlo = int(min(s[0] for s in spans))
    xhi = int(math.ceil(max(s[1] for s in spans)))
    if xhi > w:
        xhi = w
    n = xhi - xlo
    if n <= 0:
        return None
    cov = [0.0] * n
    if len(spans) == SS:
        la = max(s[0] for s in spans)
        rb = min(s[1] for s in spans)
        f0 = int(math.ceil(la)) - xlo
        f1 = int(math.floor(rb)) - xlo
        if f0 < 0:
            f0 = 0
        if f1 > n:
            f1 = n
        if f1 <= f0:
            f0 = f1 = 0
    else:
        f0 = f1 = 0
    if f1 > f0:
        cov[f0:f1] = [1.0] * (f1 - f0)
    inv = 1.0 / SS
    for rng in ((0, f0), (f1, n)):
        for j in range(rng[0], rng[1]):
            X = xlo + j
            X1 = X + 1
            c = 0.0
            for xa, xb in spans:
                lo = xa if xa > X else X
                hi = xb if xb < X1 else X1
                if hi > lo:
                    c += hi - lo
            v = c * inv
            cov[j] = 1.0 if v > 1.0 else v
    return (xlo, cov, f0, f1)


BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def fill(cv, shape, color=None, grad=None, alpha=1.0, alpha_grad=None,
         clip=None, ylim=None):
    """把 shape 填到画布上。grad=(c0,c1,y0,y1) 竖向线性渐变;alpha_grad 同理。

    渐变走 4x4 有序抖动:8bit 下 #232326->#141416 只有 15 级,824 行直接四舍五入
    会出横向色带。抖动后每行是 4 像素一循环的图案,整段仍可切片赋值,不掉速度。
    """
    w = cv.w; h = cv.h; buf = cv.buf
    ya, yb = shape.bounds()
    if ylim is not None:
        if ylim[0] > ya:
            ya = ylim[0]
        if ylim[1] < yb:
            yb = ylim[1]
    iy0 = max(0, int(math.floor(ya)))
    iy1 = min(h, int(math.ceil(yb)))
    for y in range(iy0, iy1):
        yc = y + 0.5
        if grad is not None:
            c0, c1, gy0, gy1 = grad
            t = (yc - gy0) / (gy1 - gy0) if gy1 > gy0 else 0.0
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            fs = [c0[i] + (c1[i] - c0[i]) * t for i in range(3)]
            brow = BAYER4[y & 3]
            ph = []
            for xp in range(4):
                th = (brow[xp] + 0.5) / 16.0
                px = []
                for v in fs:
                    iv = int(v)
                    if v - iv > th:
                        iv += 1
                    px.append(255 if iv > 255 else (0 if iv < 0 else iv))
                ph.append(tuple(px))
        else:
            ph = [tuple(color)] * 4
        a = alpha
        if alpha_grad is not None:
            a0, a1, ay0, ay1 = alpha_grad
            t = (yc - ay0) / (ay1 - ay0) if ay1 > ay0 else 0.0
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            a = alpha * (a0 + (a1 - a0) * t)
        if a <= 0.0008:
            continue
        res = _row_cov(shape, y, w, clip)
        if res is None:
            continue
        xlo, cov, f0, f1 = res
        n = len(cov)
        rowbase = y * w
        opaque = a >= 0.999
        if opaque and f1 > f0:
            L = f1 - f0
            start = xlo + f0
            pat = b''.join(bytes((c[0], c[1], c[2], 255))
                           for c in ph[start & 3:] + ph[:start & 3])
            run = (pat * (L // 4 + 2))[:L * 4]
            i = (rowbase + start) * 4
            buf[i:i + len(run)] = run
            parts = ((0, f0), (f1, n))
        else:
            parts = ((0, n),)
        for pa, pb in parts:
            for j in range(pa, pb):
                c = cov[j]
                if c <= 0.0015:
                    continue
                aa = c * a
                X = xlo + j
                r, g, b = ph[X & 3]
                i = (rowbase + X) * 4
                da = buf[i + 3]
                if da == 0:
                    buf[i] = r; buf[i + 1] = g; buf[i + 2] = b
                    buf[i + 3] = int(aa * 255 + 0.5)
                else:
                    df = da * (1.0 / 255.0) * (1.0 - aa)
                    oa = aa + df
                    if oa <= 0.0:
                        continue
                    iv = 1.0 / oa
                    v0 = int((r * aa + buf[i] * df) * iv + 0.5)
                    v1 = int((g * aa + buf[i + 1] * df) * iv + 0.5)
                    v2 = int((b * aa + buf[i + 2] * df) * iv + 0.5)
                    buf[i] = 255 if v0 > 255 else v0
                    buf[i + 1] = 255 if v1 > 255 else v1
                    buf[i + 2] = 255 if v2 > 255 else v2
                    va = int(oa * 255 + 0.5)
                    buf[i + 3] = 255 if va > 255 else va


def raster_grid(shape, w, h, ss=2):
    """把形状栅格化成 w*h 的浮点覆盖率网格(给投影用)。"""
    g = [0.0] * (w * h)
    ya, yb = shape.bounds()
    y0 = max(0, int(math.floor(ya)))
    y1 = min(h, int(math.ceil(yb)))
    inv = 1.0 / ss
    for y in range(y0, y1):
        base = y * w
        for s in range(ss):
            sp = shape.span(y + (s + 0.5) / ss)
            if sp is None:
                continue
            xa, xb = sp
            if xa < 0.0:
                xa = 0.0
            if xb > w:
                xb = float(w)
            if xb <= xa:
                continue
            ia = int(xa); fa = xa - ia
            ib = int(xb); fb = xb - ib
            if fb == 0.0 and ib > ia:
                ib -= 1; fb = 1.0
            if ib >= w:
                ib = w - 1; fb = 1.0
            if ia == ib:
                g[base + ia] += (fb - fa) * inv
            else:
                g[base + ia] += (1.0 - fa) * inv
                g[base + ib] += fb * inv
                for x in range(base + ia + 1, base + ib):
                    g[x] += inv
    return g


def box_blur(g, w, h, r, passes=3):
    win = 2 * r + 1
    iw = 1.0 / win
    for _ in range(passes):
        ng = [0.0] * (w * h)
        for y in range(h):
            b = y * w
            acc = 0.0
            for x in range(-r, r + 1):
                acc += g[b + (0 if x < 0 else (w - 1 if x > w - 1 else x))]
            for x in range(w):
                ng[b + x] = acc * iw
                xm = x - r
                xp = x + r + 1
                acc -= g[b + (0 if xm < 0 else (w - 1 if xm > w - 1 else xm))]
                acc += g[b + (0 if xp < 0 else (w - 1 if xp > w - 1 else xp))]
        g2 = [0.0] * (w * h)
        for x in range(w):
            acc = 0.0
            for y in range(-r, r + 1):
                acc += ng[(0 if y < 0 else (h - 1 if y > h - 1 else y)) * w + x]
            for y in range(h):
                g2[y * w + x] = acc * iw
                ym = y - r
                yp = y + r + 1
                acc -= ng[(0 if ym < 0 else (h - 1 if ym > h - 1 else ym)) * w + x]
                acc += ng[(0 if yp < 0 else (h - 1 if yp > h - 1 else yp)) * w + x]
        g = g2
    return g


# --------------------------------------------------------------------------
# 五、设计参数(设计空间 1024,图形占中间 824,四边各留 100)
# --------------------------------------------------------------------------

PLATE_M = 100.0          # 四边留白(图形占中间 824,与苹果 macOS 模板一致)
# 底板圆角:实测 Notes / Reminders / 系统模板的 .icns,左边界轮廓对
# |dx/r|^n + |dy/r|^n = 1 做最小二乘,得 n=3.0、r/S=0.3158(RMS 0.91px)。
# n=5、r/S=0.2245 那组的 RMS 是 11.6px,画出来明显方,故按实测取值。
PLATE_R = 260.0          # 260/824 = 0.3155
PLATE_N = 3.0

C_PLATE_TOP = rgb('#232326')
C_PLATE_BOT = rgb('#141416')
C_PLATE_FLAT = rgb('#1D1D20')
C_CARD_TOP = rgb('#F6F3ED')
C_CARD_BOT = rgb('#E9E4DA')
C_CARD_FLAT = rgb('#F2EFE9')
C_GOLD_TOP = rgb('#DCB36C')
C_GOLD_BOT = rgb('#C6913F')
C_GOLD_FLAT = rgb('#D0A252')
C_LINE_1 = rgb('#ADA69A')
C_LINE_2 = rgb('#CBC4B7')

# 三档:card_w card_h tab_up tab_w tab_inset tab_dep card_r tab_r
TIERS = {
    'full':  dict(card_w=580, card_h=486, tab_up=84,  tab_w=186, tab_inset=48,
                  tab_dep=48, card_r=34, tab_r=26, y_off=0,
                  lines=True, gloss=True, shadow=True, grad=True),
    'mid':   dict(card_w=604, card_h=500, tab_up=116, tab_w=236, tab_inset=46,
                  tab_dep=70, card_r=36, tab_r=28, y_off=0,
                  lines=False, gloss=False, shadow=True, grad=True),
    # micro 档的数字是倒推出来的:先定死 16px 下希望落在哪几个像素上
    # (底板 2..14、金标签 y 3..5、卡片 y 5..12 / x 4..12),再乘 64 回设计空间。
    'micro': dict(card_w=512, card_h=448, tab_up=128, tab_w=216, tab_inset=40,
                  tab_dep=96, card_r=40, tab_r=34, y_off=-32,
                  lines=False, gloss=False, shadow=False, grad=False),
}


def tier_of(size):
    if size >= 160:
        return 'full'
    if size >= 40:
        return 'mid'
    return 'micro'


def geometry(size):
    """返回该尺寸下的全部形状(已换算到目标像素坐标,并对齐像素栅格)。"""
    t = tier_of(size)
    p = TIERS[t]
    k = size / 1024.0

    def sn(v):
        """设计坐标 -> 目标像素坐标,并吸附到整像素。"""
        return round(v * k)

    def sr(v):
        """半径:吸附到整像素,至少 0。"""
        return max(0.0, round(v * k))

    px0 = sn(PLATE_M)
    px1 = size - px0
    py0 = px0
    py1 = px1

    cw = p['card_w']; ch = p['card_h']; up = p['tab_up']
    top = 512.0 - (ch + up) / 2.0 + p.get('y_off', 0)
    d_card_y0 = top + up
    d_card_y1 = d_card_y0 + ch
    d_card_x0 = 512.0 - cw / 2.0
    d_card_x1 = 512.0 + cw / 2.0
    d_tab_x1 = d_card_x1 - p['tab_inset']
    d_tab_x0 = d_tab_x1 - p['tab_w']
    d_tab_y1 = d_card_y0 + p['tab_dep']

    cx0 = sn(d_card_x0); cx1 = sn(d_card_x1)
    cy0 = sn(d_card_y0); cy1 = sn(d_card_y1)
    tx0 = sn(d_tab_x0);  tx1 = sn(d_tab_x1)
    ty0 = sn(top);       ty1 = sn(d_tab_y1)

    g = {
        'tier': t, 'k': k, 'p': p,
        'plate': RRect(px0, py0, px1, py1, sr(PLATE_R), PLATE_N),
        'card': RRect(cx0, cy0, cx1, cy1, sr(p['card_r']), 3.2),
        'tab': RRect(tx0, ty0, tx1, ty1, sr(p['tab_r']), 3.0),
        'plate_box': (px0, py0, px1, py1),
        'card_box': (cx0, cy0, cx1, cy1),
        'tab_box': (tx0, ty0, tx1, ty1),
    }

    if p['lines']:
        lx0 = d_card_x0 + 66.0
        specs = [(470.0, 30.0, 420.0, C_LINE_1),
                 (552.0, 24.0, 340.0, C_LINE_2),
                 (634.0, 24.0, 238.0, C_LINE_2)]
        lines = []
        for cy, hh, ww, col in specs:
            y0 = sn(cy - hh / 2.0); y1 = sn(cy + hh / 2.0)
            x0 = sn(lx0); x1 = sn(lx0 + ww)
            if y1 <= y0:
                y1 = y0 + 1
            lines.append((RRect(x0, y0, x1, y1, (y1 - y0) / 2.0, 2.0), col))
        g['lines'] = lines
    else:
        g['lines'] = []
    return g


# --------------------------------------------------------------------------
# 六、投影(低分辨率栅格 + 盒式模糊 + 双线性放大)
# --------------------------------------------------------------------------

def draw_shadow(cv, plate, size):
    """底板下方一层很淡的外投影,压在 100 的留白里,不出画布。"""
    k = size / 1024.0
    dy = 14.0 * k
    blur = 20.0 * k
    if blur < 1.0:
        return
    lw = max(48, int(size / 4))
    scale = lw / float(size)
    r = max(1, int(round(blur * scale)))
    g = raster_grid(Scaled(Shifted(plate, 0.0, dy), scale), lw, lw, ss=3)
    g = box_blur(g, lw, lw, r, passes=3)
    peak = max(g) or 1.0
    amp = 0.26 / peak
    pb0, pb1 = plate.bounds()
    span_h = (pb1 - pb0) or 1.0
    buf = cv.buf
    y_from = max(0, int(plate.bounds()[0] + dy - blur * 2))
    for y in range(y_from, size):
        sp = plate.span(y + 0.5)
        if sp is not None:
            ia = int(math.ceil(sp[0])); ib = int(math.floor(sp[1]))
            rngs = ((0, min(ia, size)), (max(ib, 0), size))
        else:
            rngs = ((0, size),)
        # 竖向权重:上缘几乎不投,越往下越实,做成落在桌面上的样子
        wgt = 0.18 + 0.82 * ((y + 0.5) - pb0) / span_h
        if wgt < 0.0:
            wgt = 0.0
        elif wgt > 1.0:
            wgt = 1.0
        fy = (y + 0.5) * scale - 0.5
        y0 = int(math.floor(fy))
        wy = fy - y0
        y0c = 0 if y0 < 0 else (lw - 1 if y0 > lw - 1 else y0)
        y1c = 0 if y0 + 1 < 0 else (lw - 1 if y0 + 1 > lw - 1 else y0 + 1)
        rb0 = y0c * lw
        rb1 = y1c * lw
        for xa, xb in rngs:
            for x in range(xa, xb):
                fx = (x + 0.5) * scale - 0.5
                x0 = int(math.floor(fx))
                wx = fx - x0
                x0c = 0 if x0 < 0 else (lw - 1 if x0 > lw - 1 else x0)
                x1c = 0 if x0 + 1 < 0 else (lw - 1 if x0 + 1 > lw - 1 else x0 + 1)
                v = ((g[rb0 + x0c] * (1 - wx) + g[rb0 + x1c] * wx) * (1 - wy)
                     + (g[rb1 + x0c] * (1 - wx) + g[rb1 + x1c] * wx) * wy)
                a = v * amp * wgt
                if a <= 0.004:
                    continue
                i = (y * size + x) * 4
                av = int(a * 255 + 0.5)
                if av > buf[i + 3]:
                    buf[i + 3] = 255 if av > 255 else av


# --------------------------------------------------------------------------
# 七、渲染
# --------------------------------------------------------------------------

def render(size, layer='all'):
    """layer: all 完整图 / bg 背景层(底板) / fg 前景层(卡片+标签+文字线)。"""
    g = geometry(size)
    p = g['p']
    cv = Canvas(size, size)
    px0, py0, px1, py1 = g['plate_box']
    cx0, cy0, cx1, cy1 = g['card_box']

    if layer in ('all', 'bg'):
        if layer == 'all' and p['shadow']:
            draw_shadow(cv, g['plate'], size)
        if p['grad']:
            fill(cv, g['plate'], grad=(C_PLATE_TOP, C_PLATE_BOT, py0, py1))
        else:
            fill(cv, g['plate'], color=C_PLATE_FLAT)
        if p['gloss']:
            band = max(2.0, 6.0 * g['k'])
            fill(cv, g['plate'], color=(255, 255, 255),
                 alpha_grad=(0.10, 0.0, py0, py0 + band),
                 ylim=(py0, py0 + band))

    if layer in ('all', 'fg'):
        # 标签先画,卡片盖住它下半截,接缝干净
        if p['grad']:
            fill(cv, g['tab'], grad=(C_GOLD_TOP, C_GOLD_BOT,
                                     g['tab_box'][1], cy0))
        else:
            fill(cv, g['tab'], color=C_GOLD_FLAT)
        if p['grad']:
            fill(cv, g['card'], grad=(C_CARD_TOP, C_CARD_BOT, cy0, cy1))
        else:
            fill(cv, g['card'], color=C_CARD_FLAT)
        if p['gloss']:
            band = max(2.0, 7.0 * g['k'])
            fill(cv, g['card'], color=(255, 255, 255),
                 alpha_grad=(0.55, 0.0, cy0, cy0 + band),
                 ylim=(cy0, cy0 + band))
        for shape, col in g['lines']:
            fill(cv, shape, color=col)
    return cv


# --------------------------------------------------------------------------
# 八、尺寸对照图(自带 5x7 数字点阵,不依赖系统字体)
# --------------------------------------------------------------------------

GLYPH = {
    '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
    '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
    '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
    '3': ['11110', '00001', '00001', '01110', '00001', '00001', '11110'],
    '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
    '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
    '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
    '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
    '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
    '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
    'x': ['00000', '00000', '10001', '01010', '00100', '01010', '10001'],
    'p': ['11110', '10001', '10001', '11110', '10000', '10000', '10000'],
    't': ['01000', '01000', '11100', '01000', '01000', '01001', '00110'],
    ' ': ['00000', '00000', '00000', '00000', '00000', '00000', '00000'],
}


def text_w(s, scale):
    return (len(s) * 6 - 1) * scale


def draw_text(cv, s, x, y, scale, color):
    for ch in s:
        pat = GLYPH.get(ch)
        if pat is None:
            x += 6 * scale
            continue
        for ry, row in enumerate(pat):
            for rx, c in enumerate(row):
                if c == '1':
                    fill_rect(cv, x + rx * scale, y + ry * scale,
                              x + (rx + 1) * scale, y + (ry + 1) * scale, color)
        x += 6 * scale


def build_contact_sheet(path):
    """16/32/64/128/512 按真实像素并排,浅底一排、深底一排,再加一排 8 倍放大。"""
    sizes = [16, 32, 64, 128, 512]
    icons = {s: render(s) for s in sizes}
    gap = 48
    pad = 48
    ls = 3                      # 标注字号倍率
    lab_h = 7 * ls
    row_w = sum(sizes) + gap * (len(sizes) - 1)
    W = row_w + pad * 2
    band1 = pad + 512 + 16 + lab_h + pad
    band3_top = 256
    band3 = pad + band3_top + 16 + lab_h + pad
    H = band1 * 2 + band3
    cv = Canvas(W, H)

    fill_rect(cv, 0, 0, W, band1, rgb('#F2F2F4'))
    fill_rect(cv, 0, band1, W, band1 * 2, rgb('#1B1B1D'))
    fill_rect(cv, 0, band1 * 2, W, H, rgb('#8A8A8E'))

    for bi, (top, lab_col) in enumerate(((0, rgb('#3C3C43')),
                                         (band1, rgb('#D8D8DC')))):
        base = top + pad + 512      # 共同基线
        x = pad
        for s in sizes:
            blit(cv, icons[s], x, base - s)
            t = str(s)
            draw_text(cv, t, x + (s - text_w(t, ls)) // 2, base + 16, ls, lab_col)
            x += s + gap

    top = band1 * 2
    base = top + pad + band3_top
    x = pad
    for s, k in ((16, 8), (32, 8)):
        big = nn_scale(icons[s], k)
        blit(cv, big, x, base - big.h)
        t = '%dx%d' % (s, k)
        draw_text(cv, t, x + (big.w - text_w(t, ls)) // 2, base + 16, ls,
                  rgb('#1B1B1D'))
        x += big.w + gap
    cv.save(path)
    return cv


# --------------------------------------------------------------------------
# 九、iconset 与 icns
# --------------------------------------------------------------------------

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


def build_icns(cache):
    if os.path.isdir(ISET):
        shutil.rmtree(ISET)
    os.makedirs(ISET)
    for name, size in ICONSET:
        cache[size].save(os.path.join(ISET, name))
    # iconutil 要求输入目录带 .iconset 后缀,契约里目录名是 _iconset,这里临时复制一份
    tmp = ISET + '.iconset'
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    shutil.copytree(ISET, tmp)
    out = os.path.join(HERE, 'icon.icns')
    try:
        subprocess.run(['/usr/bin/iconutil', '-c', 'icns', tmp, '-o', out],
                       check=True, capture_output=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# --------------------------------------------------------------------------
# 十、主流程
# --------------------------------------------------------------------------

def main(argv):
    preview_only = '--preview' in argv
    made = []

    def note(path):
        made.append((path, os.path.getsize(path)))
        print('  %-58s %8.1f KB' % (os.path.basename(path),
                                    os.path.getsize(path) / 1024.0))

    if preview_only:
        print('渲染预览与分层图')
        p = os.path.join(HERE, 'icon_v2_索引卡_1024_%s.png' % STAMP)
        render(1024).save(p); note(p)
        p = os.path.join(HERE, 'icon_v2_层_背景_1024.png')
        render(1024, layer='bg').save(p); note(p)
        p = os.path.join(HERE, 'icon_v2_层_前景_1024.png')
        render(1024, layer='fg').save(p); note(p)
        p = os.path.join(HERE, 'icon_v2_尺寸对照_%s.png' % STAMP)
        build_contact_sheet(p); note(p)
        print('\n--preview:只出预览大图,已完成。')
        return 0

    print('渲染各档位图')
    cache = {}
    for _, size in ICONSET:
        if size not in cache:
            cache[size] = render(size)
            print('  %4d px  档位 %s' % (size, tier_of(size)))

    print('写 _iconset 与 icon.icns')
    out = build_icns(cache)
    note(out)

    print('写 PWA 图标')
    for size, name in ((192, 'icon-192.png'), (512, 'icon-512.png')):
        cv = cache.get(size) or render(size)
        p = os.path.join(HERE, name)
        cv.save(p); note(p)

    print('\n共 %d 个产出文件。' % len(made))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
