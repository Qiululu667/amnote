#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AM·Note 界面词典校验。

从 src/template.html 里把所有「取键位置」抠出来，跟 src/locales/*.js 对一遍：
谁缺了、谁多了、一共多少条。纯标准库，Python 3.9+。

取键位置一共四种，改了页面就得同步改这里：
  1. TR('…')  —— 页面里统一用的翻译函数（t 被一堆叫 t 的局部变量挡住了）
  2. t('…')   —— 契约里的名字，别的模块／控制台可能直接用
  3. K('…')   —— 只做取键标记：这一串先原样留着，晚一点才 TR() 显示
  4. <body> 里写死的中文文本节点，和 title/placeholder/aria-label/alt 属性
     （由页面的 bootI18n() 统一翻；「后退 ⌘[」这种只把标签部分当键）

用法：
  python3 scripts/i18n_check.py                # 校验，有缺失退出码 1
  python3 scripts/i18n_check.py --dump k.tsv   # 键 \t 行号 \t 所在行片段
  python3 scripts/i18n_check.py --pseudo en --force   # 写一份 ⟦键⟧ 伪翻译（会盖掉真词典）
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(ROOT, "src", "template.html")
LOCALE_DIR = os.path.join(ROOT, "src", "locales")
LANGS = ["en", "zh-HK"]

# 跟页面 template.html 里的 CJK 保持一致
CJK = re.compile(u"[⺀-鿿豈-﫿︰-﹏＀-｠　-〿]")
# 「标签 ＋ 快捷键」：只有标签部分算键（页面 tPhrase 用的是同一条规则）
KBD_TAIL = re.compile(u"^([\\s\\S]*?)[\\s　]+((?:[⌘⌥⇧⌃][^\\s　]*)|Esc)$")
KEY_FNS = ("TR", "t", "K")


# ── JS 扫描 ──────────────────────────────────────────────────────────
def scan_js(src, line0=1):
    """走一遍 JS，返回 [(键, 行号)]。

    自己认字符串／模板串／正则／注释：注释里写着 t('键') 这样的例子，正则里有
    /[&<>"']/ 这种带引号的字符类，而页面上大半的 TR() 是写在模板串的 ${…} 里的，
    靠 grep 要么捡错要么捡漏。模板串用一个模式栈进出，${…} 里照样当 JS 扫。
    """
    out = []
    i, n = 0, len(src)
    line = line0
    prev_sig = ""              # 上一个有意义的字符，用来判断 / 是除号还是正则
    modes = [["js", 0]]        # ["js", 花括号深度] ｜ ["tpl"]

    def literal_at(k):
        """k 指着 ' 或 "，返回 (字符串内容, 收尾引号之后的下标)。"""
        q = src[k]
        j = k + 1
        buf = []
        while j < n:
            c = src[j]
            if c == "\\":
                nxt = src[j + 1] if j + 1 < n else ""
                buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                j += 2
                continue
            if c == q:
                return "".join(buf), j + 1
            if c == "\n":
                return None, k + 1
            buf.append(c)
            j += 1
        return None, n

    while i < n:
        c = src[i]
        # ── 模板串内部：只找收尾的 ` 和 ${ ──
        if modes[-1][0] == "tpl":
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                line += 1
                i += 1
                continue
            if c == "`":
                modes.pop()
                prev_sig = "'"
                i += 1
                continue
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                modes.append(["js", 0])
                prev_sig = "{"
                i += 2
                continue
            i += 1
            continue
        # ── JS ──
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            i = j
            continue
        if c == "/" and (prev_sig == "" or prev_sig in "(,=:[!&|?{};+-*%~^<>"):
            j = i + 1
            klass = False
            ok = False
            while j < n:
                d = src[j]
                if d == "\\":
                    j += 2
                    continue
                if d == "\n":
                    break
                if d == "[":
                    klass = True
                elif d == "]":
                    klass = False
                elif d == "/" and not klass:
                    ok = True
                    j += 1
                    break
                j += 1
            if ok:
                i = j
                prev_sig = "/"
                continue
        if c == "`":
            modes.append(["tpl"])
            i += 1
            continue
        if c in "'\"":
            text, j = literal_at(i)
            line += src.count("\n", i, j)
            i = j
            prev_sig = "'"
            continue
        if c == "{":
            modes[-1][1] += 1
            prev_sig = c
            i += 1
            continue
        if c == "}":
            if modes[-1][1] == 0 and len(modes) > 1:
                modes.pop()                      # ${…} 结束，回到模板串
            else:
                modes[-1][1] = max(0, modes[-1][1] - 1)
            prev_sig = c
            i += 1
            continue
        if c.isalpha() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            word = src[i:j]
            before = src[i - 1] if i else " "
            k = j
            while k < n and src[k] in " \t\n":
                k += 1
            if (word in KEY_FNS and k < n and src[k] == "("
                    and not (before.isalnum() or before in "_$.")):
                k += 1
                while k < n and src[k] in " \t\n":
                    k += 1
                if k < n and src[k] in "'\"":
                    text, _end = literal_at(k)
                    if text is not None:
                        out.append((text, line + src.count("\n", i, k)))
            line += src.count("\n", i, j)
            i = j
            prev_sig = word[-1]
            continue
        if not c.isspace():
            prev_sig = c
        i += 1
    return out


# ── 静态 markup ──────────────────────────────────────────────────────
I18N_ATTRS = ("title", "placeholder", "aria-label", "alt")


class BodyKeys(HTMLParser):
    def __init__(self, line0):
        super().__init__(convert_charrefs=True)
        self.keys = []
        self.line0 = line0
        self.skip = 0

    def _add(self, raw):
        s = re.sub(r"\s+", " ", raw or "").strip()
        if not s or not CJK.search(s):
            return
        m = KBD_TAIL.match(s)
        if m and CJK.search(m.group(1)):
            s = m.group(1)
        self.keys.append((s, self.line0 + self.getpos()[0] - 1))

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for k, v in attrs:
            if k in I18N_ATTRS and v:
                self._add(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self._add(data)


def static_keys(src):
    at = src.find("<body>")
    if at < 0:
        return []
    end = src.find("<script>", at)
    if end < 0:
        end = len(src)
    line0 = src.count("\n", 0, at) + 1
    p = BodyKeys(line0)
    p.feed(src[at:end])
    return p.keys


# ── 词典 ─────────────────────────────────────────────────────────────
def load_locale(path):
    """切出 `= {` … `};` 之间那一段严格 JSON。"""
    if not os.path.exists(path):
        return None, "文件不存在"
    txt = io.open(path, encoding="utf-8").read()
    at = txt.find("=", txt.find("window.AMN_I18N["))
    if at < 0:
        return None, "找不到 window.AMN_I18N[...] = {...}"
    start = txt.find("{", at)
    end = txt.rfind("};")             # 文件尾巴上还可能有别的 }，认收尾的 };
    if start < 0 or end < start:
        return None, "找不到对象体"
    try:
        return json.loads(txt[start:end + 1]), None
    except Exception as e:                       # noqa: BLE001
        return None, "对象体不是严格 JSON：%s" % e


def backup_outside_repo(path, lang):
    """把词典原件备份到仓库外面，返回落点。

    不放 src/locales/xx.js.bak：那个目录 build_app.py 是整目录拷进 app 的，
    多一个 .bak 就跟着进了包，git status 也跟着脏。
    """
    dst_dir = os.path.join(tempfile.gettempdir(), "amnote-i18n-backup")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "%s.%s.js" % (lang, time.strftime("%Y%m%d-%H%M%S")))
    shutil.copy2(path, dst)
    return dst


def write_locale(path, lang, table, banner):
    lines = [banner, "window.AMN_I18N = window.AMN_I18N || {};",
             'window.AMN_I18N[%s] = {' % json.dumps(lang, ensure_ascii=False)]
    items = list(table.items())
    for n, (k, v) in enumerate(items):
        tail = "" if n == len(items) - 1 else ","
        lines.append("  %s: %s%s" % (json.dumps(k, ensure_ascii=False),
                                     json.dumps(v, ensure_ascii=False), tail))
    lines.append("};")
    lines.append("")
    io.open(path, "w", encoding="utf-8").write("\n".join(lines))


# ── 主流程 ───────────────────────────────────────────────────────────
def main_script(src):
    """主 <script>（就是 const DATA 那一段）的正文和它的起始行号。"""
    lines = src.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "<script>" and i + 1 < len(lines) and "const DATA" in lines[i + 1]:
            start = i + 1
            break
    if start is None:
        return "", 1
    end = len(lines)
    for i in range(len(lines) - 1, start, -1):
        if lines[i].strip() == "</script>":
            end = i
            break
    return "\n".join(lines[start:end]), start + 1


def collect():
    src = io.open(TEMPLATE, encoding="utf-8").read()
    body, line0 = main_script(src)
    found = scan_js(body, line0) + static_keys(src)
    order, first = [], {}
    for key, line in found:
        if key not in first:
            first[key] = line
            order.append(key)
    lines = src.split("\n")
    snippet = {k: lines[first[k] - 1].strip()[:120] for k in order}
    return order, first, snippet


def main():
    ap = argparse.ArgumentParser(description="AM·Note 界面词典校验")
    ap.add_argument("--dump", metavar="FILE", help="导出 键\\t行号\\t所在行片段（给翻译用）")
    ap.add_argument("--pseudo", metavar="LANG", help="写一份 ⟦键⟧ 伪翻译词典（用来找漏包的中文）")
    ap.add_argument("--force", action="store_true", help="--pseudo 时允许盖掉已有的词典")
    ap.add_argument("--quiet", action="store_true", help="只报数字，不列具体的键")
    args = ap.parse_args()

    keys, first, snippet = collect()
    print("模板里一共 %d 个键（%s）" % (len(keys), os.path.relpath(TEMPLATE, ROOT)))

    if args.dump:
        with io.open(args.dump, "w", encoding="utf-8") as f:
            for k in keys:
                f.write("%s\t%d\t%s\n" % (k.replace("\t", " "), first[k], snippet[k]))
        print("已写出 %s" % args.dump)
        return 0

    if args.pseudo:
        # 伪翻译是整份覆盖，手上那份真译文一句都不留。默认不许盖。
        lang = args.pseudo
        path = os.path.join(LOCALE_DIR, "%s.js" % lang)
        exists = os.path.exists(path)
        if exists and not args.force:
            print("%s 已经有了，伪翻译会把它整份盖掉。真要盖加 --force（原件先备份到仓库外面）。"
                  % os.path.relpath(path, ROOT))
            return 1
        backup = backup_outside_repo(path, lang) if exists else None
        table = {k: u"⟦%s⟧" % k.split("##")[0] for k in keys}
        write_locale(path, lang, table,
                     u"// 伪翻译（i18n_check.py --pseudo %s 生成）。"
                     u"没被 ⟦⟧ 包住的中文＝还没包进 t() 的。" % lang)
        if backup:
            print("原件已备份到 %s" % backup)
        print("已写出伪翻译 %s（%d 条）" % (path, len(table)))
        return 0

    bad = 0
    want = set(keys)
    for lang in LANGS:
        path = os.path.join(LOCALE_DIR, "%s.js" % lang)
        table, err = load_locale(path)
        if table is None:
            print("[%s] 读不出：%s" % (lang, err))
            bad = 1
            continue
        have = set(table.keys())
        miss = [k for k in keys if k not in have]
        extra = sorted(have - want)
        print("[%s] 词典 %d 条 · 缺 %d · 多 %d" % (lang, len(have), len(miss), len(extra)))
        if miss and not args.quiet:
            for k in miss:
                print("   缺  %s\t(第 %d 行)" % (k, first[k]))
        if extra and not args.quiet:
            for k in extra:
                print("   多  %s" % k)
        if miss:
            bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main())
