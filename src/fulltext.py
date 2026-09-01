#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AM·Note · 全文索引与变更流水 v3  (2026-08-29)

v3 动了四处。**收录口径、db schema、流水的字段和 /__changes 的语义都没变。**
    · search()        文件名进匹配 ＋ 两档排序（见函数注释）
    · _extract_html() 保住 <title>、丢掉 <template> 与超长 base64 块
    · 门户写入活表   判「门户／外部」改成判定当刻加锁查活表并消费，不再吃
                     调用方在起线程那一刻拷的快照（v20 那个误记 bug）。
                     sync() 因此不再收 portal_writes 参数。
    · 流水降噪       同一份文件、来源是门户、离上一条不足 10 分钟的连续「修改」
                     合并进上一条（只更新 时间 / 大小KB，序号不动），
                     不再一行行往下堆。外部改动一条都不合。
这两条是给自动保存铺路的：停笔 2 秒落一次盘，不修的话流水会变成一片心跳，
而且每一次保存都可能被判成「外部」，白留一版档。
html 那一改要对已经进库的 html 补一次重抽（只重写 正文 列），
跟 --compact 一样不动 mtime / 大小 / 流水。

一个模块管五件事，portal_server 从这里取：

    0. 收录规则   库根、config.json、跳过 / 噪声规则、附件后缀。v2 起搬到这里：
                  scan_tags.py 退役之后，这套规则没有别的家可回，而索引和门户
                  必须共用同一份口径，两边各写一份迟早对不上。
    1. 全文索引   md / html 的正文、xlsx / csv 的文字单元格、pdf 的文本层，
                  全部收进 .amnote/fulltext.db，给搜索用。中文检索用子串匹配（LIKE），
                  不做分词——两个字的词（退货、话术）分词方案都接不住，子串全能接。
    2. 变更流水   每次同步跟上一次的快照比，新增 / 修改 / 删除逐条落
                  .amnote/changes.jsonl。「昨天动了哪些文件」从这里来。
    3. 外部覆写留档  门户外（Agent、别的编辑器）改掉或删掉一份 md 时，
                  把改动前的原文（存在库里的上一版）写进 .amnote/backups/，
                  跟门户编辑的备份同一个目录、同一套命名和限额。只管 md：
                  html 多是生成物，附件是二进制，都留不了也不该留。
    4. 链接图     md 正文里指向库内 md / html 的链接和 [[题名]]，存进 链接 表，
                  给 --backlinks（谁引用了这份）和 --deadlinks（指到不存在的文件）用。

**这个模块自己不开任何 HTTP 路由，也绝不写库里的业务文件。**
它写的只有所选文件夹 .amnote/ 下的三样：fulltext.db、changes.jsonl、backups/ 里的留档。
「能改库内产出的写路由只有 /__save 一条」这条规矩不因它而变。
库根来自 --root 或环境变量 AMNOTE_VAULT，import 之后、用 ROOT 之前必须
调用一次 configure()。.amnote 不进索引。

数据层口径：
    · 收录范围＝跳过规则＋噪声规则，不是全盘。噪声目录里的改动不进流水——
      那些本来就不是产出。
    · **xlsx / csv 只索引表头和前 200 行，且单份不超过 8000 字**（2026-08-25 起）。
      原来整份抽 2000 行，csv 62.5 MB ＋ xlsx 59.7 MB 占了索引的 82%，而这两类
      在门户里根本不渲染，索引它们只为「搜得到、知道有这么一份」。宽表会先撞上
      8000 字那道闸、收不满 200 行，理由见 SHEET_TEXT_CHARS 上面那段。
      要恢复就把这两个数调回去，再跑一次 `--compact`。
    · db 和 jsonl 都是派生物加软状态：删掉 db 只是全文索引重建一次；
      删掉 jsonl 只是历史流水没了。业务文件一根毛不掉。

用法（都能单独跑，不用起服务；--root 可写在任意位置）：
    python3 fulltext.py --root /path --sync
    python3 fulltext.py --root /path --search 关键词
    python3 fulltext.py --root /path --status
    python3 fulltext.py --root /path --compact
    python3 fulltext.py --root /path --backlinks 路径
    python3 fulltext.py --root /path --deadlinks
"""

import codecs
import csv
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))

ROOT = None
CONFIG_PATH = None
DB_PATH = None
JOURNAL = None
BACKUP_DIR = None                                 # 跟 portal_server 的编辑备份同一个
BACKUP_KEEP = 10                                  # 每份文件留几版，跟编辑备份同额
BACKUP_TOTAL_MB = 500                             # 备份目录总大小上限，超了删最旧的


def take_root_arg(argv):
    """抽出 --root PATH，可写在任意位置。返回 (root 或 None, 剩余参数)。"""
    root = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--root":
            if i + 1 >= len(argv):
                print("请在 --root 后面写下文件夹的路径。", file=sys.stderr)
                sys.exit(1)
            root = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return root, rest


def configure(root=None):
    """定库根，并把索引 / 流水 / 备份 / 配置指到 {vault}/.amnote/。

    必须在 import 之后、用 ROOT 之前调一次。库根来自参数或环境变量
    AMNOTE_VAULT，不再往上找标志文件。没给就在 stderr 说明原因后退出。
    """
    global ROOT, CONFIG_PATH, DB_PATH, JOURNAL, BACKUP_DIR
    raw = (root if root is not None else "") or os.environ.get("AMNOTE_VAULT") or ""
    raw = str(raw).strip()
    if not raw:
        print("没有库根。请用 --root 指定一个文件夹，或设置环境变量 AMNOTE_VAULT。",
              file=sys.stderr)
        sys.exit(1)
    ROOT = os.path.abspath(os.path.expanduser(raw))
    if os.path.exists(ROOT) and not os.path.isdir(ROOT):
        print(f"这不是文件夹：{ROOT}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(ROOT):
        print(f"找不到这个文件夹：{ROOT}", file=sys.stderr)
        sys.exit(1)
    amdir = os.path.join(ROOT, ".amnote")
    try:
        os.makedirs(amdir, exist_ok=True)
    except OSError as e:
        print(f"建不了 {amdir}：{e}", file=sys.stderr)
        sys.exit(1)
    CONFIG_PATH = os.path.join(amdir, "config.json")
    DB_PATH = os.path.join(amdir, "fulltext.db")
    JOURNAL = os.path.join(amdir, "changes.jsonl")
    BACKUP_DIR = os.path.join(amdir, "backups")
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except OSError as e:
        print(f"建不了 {BACKUP_DIR}：{e}", file=sys.stderr)
        sys.exit(1)
    return ROOT


# 抽正文的上限。索引是「找得到」用的，不是照单全收：超长的截断并在 备注 里说明。
MD_MAX_BYTES = 4_000_000
HTML_READ_BYTES = 8_000_000
HTML_TEXT_CHARS = 300_000
SHEET_MAX_SHEETS = 5
# 表格只抽表头 ＋ 前 200 行（2026-08-25）。这四个数就是「索引截断」那条闸，
# 调大之后要跑一次 --compact 才会重抽，光 --sync 认不出内容变了（mtime 没动）。
TBL_MAX_ROWS = 200
TBL_MAX_COLS = 40
TBL_MAX_CHARS = 200        # 单格字数。评论、宝贝标题这类字段动辄几百字
# 单份表格进索引的字数上限。比 200 行更早生效：宽表一行很长时，实际收不满 200 行。
# 订单号、金额这类格子没人拿来当检索词，表头和头几十行才把文件区分开。
SHEET_TEXT_CHARS = 8_000
PDF_TEXT_CHARS = 500_000
PDF_BATCH = 12                                    # 一次 osascript 处理几份 pdf
PDF_TIMEOUT = 180

_sync_lock = threading.Lock()
_state = {"运行中": False, "待重跑": False}


# ── 配置：收录规则的唯一来源 ─────────────────────────────────
#
# config.json 是唯一该动的地方。这里留一份同样的默认值，是为了配置文件被删、
# 被改坏、或者少了某一项时还能跑——缺哪项回退哪项，不整份放弃。
#
# 2026-08-25 结构精简：主题清单 / 目录默认主题 / 默认主题 / 索引文件 四项随标签
# 体系一起退役，这里不再认。老的 config.json 里还留着这几个键也没关系，读进来
# 直接忽略，写回时原样留着，不报错也不删配置文件里多出来的键。

DEFAULTS = {
    "跳过目录关键词": [
        ".git", "node_modules", "dist", "__pycache__", ".amnote",
        ".obsidian", "归档", "历史版本", "备份", ".claude", ".codex",
    ],
    "噪声目录": ["node_modules", "dist", "output", "outputs", "__pycache__"],
    "噪声文件": [".DS_Store", "template.html"],
    "通用标题": ["README", "index", "先读我", "索引"],
    "板块名": {},
    "端口范围": [8870, 8900],
    "随手记目录": "随手记",
}

# 端口范围空了服务起不来，配置里给了空值就当没给。板块名允许空：
# 树用文件夹名当显示名。其余的（跳过、噪声、通用标题、随手记目录）允许清空。
_MUST_FILL = ("端口范围",)


def load_config(path=None):
    """读 config.json，逐项校验。返回 (配置, 问题列表)。"""
    if path is None:
        path = CONFIG_PATH
    cfg = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in DEFAULTS.items()}
    problems = []
    if not path or not os.path.exists(path):
        return cfg, problems
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        problems.append(f"config.json 读不了，整份用默认值：{e}")
        return cfg, problems
    if not isinstance(raw, dict):
        problems.append("config.json 不是一个对象，整份用默认值")
        return cfg, problems

    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        val = raw[key]
        if not isinstance(val, type(default)):
            problems.append(f"config.json 的「{key}」类型不对，这一项用默认值")
            continue
        if not val and key in _MUST_FILL:
            problems.append(f"config.json 的「{key}」是空的，这一项用默认值")
            continue
        cfg[key] = val

    pr = cfg["端口范围"]
    if not (len(pr) == 2 and all(isinstance(x, int) for x in pr)
            and 1 <= pr[0] <= pr[1] <= 65535):
        problems.append(f"config.json 的「端口范围」不合法（{pr}），用默认值")
        cfg["端口范围"] = list(DEFAULTS["端口范围"])
    return cfg, problems


_rules_cache = {"mtime": None, "值": None}


def rules():
    """(跳过目录关键词, 噪声目录, 噪声文件)，跟着 config.json 的 mtime 走。

    老版本在 import 时把规则读死一次，改完设置得退出 app 才认。现在改完点一次
    重扫就生效——设置面板上那四项本来就是「改了要马上看效果」的东西。
    """
    try:
        m = os.path.getmtime(CONFIG_PATH)
    except OSError:
        m = 0
    if _rules_cache["mtime"] != m:
        c, _ = load_config()
        _rules_cache["值"] = (tuple(c["跳过目录关键词"]),
                              tuple(c["噪声目录"]), tuple(c["噪声文件"]))
        _rules_cache["mtime"] = m
    return _rules_cache["值"]


# 附件＝看得了、但不进正文渲染的文件。值是显示用的类型名。
ATT_EXT = {
    ".pdf": "pdf", ".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xls",
    ".csv": "csv", ".tsv": "csv", ".docx": "docx", ".pptx": "pptx",
}


def should_skip(relpath: str) -> bool:
    """路径上任一段命中跳过关键词，或文件名以 _ / . 开头 → 不收。
    .amnote 硬跳过，不进索引。"""
    skip_dir, _, _ = rules()
    parts = relpath.replace("/", os.sep).split(os.sep)
    if any(p == ".amnote" for p in parts):
        return True
    for p in parts[:-1]:
        for tok in skip_dir:
            if tok in p:
                return True
    return parts[-1].startswith(("_", "."))


def is_noise(rel_u: str) -> bool:
    """噪声目录要整段同名（不是包含），噪声文件要整个文件名相同。"""
    _, noise_dir, noise_file = rules()
    parts = rel_u.split("/")
    if parts[-1] in noise_file:
        return True
    return any(seg in noise_dir for seg in parts[:-1])


# ── 库文件遍历 ──────────────────────────────────────────────

def walk_files():
    """收录范围＝跳过规则＋噪声规则＋附件后缀。
    返回 {相对路径: (mtime, 大小, 类型)}。类型是 md / html / pdf / xlsx / csv /
    xls / docx / pptx（后三种收进流水但不抽正文——没有靠谱的抽取器）。"""
    skip_dir, _, _ = rules()
    out = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d != ".amnote"
                       and not any(tok in d for tok in skip_dir)]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            is_doc = fn.endswith((".md", ".html"))
            if not is_doc and ext not in ATT_EXT:
                continue
            if fn.startswith("~$"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            if should_skip(rel):
                continue
            rel_u = rel.replace(os.sep, "/")
            if is_noise(rel_u):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            kind = ("md" if fn.endswith(".md") else
                    "html" if fn.endswith(".html") else ATT_EXT[ext])
            out[rel_u] = (round(st.st_mtime, 2), st.st_size, kind)
    return out


def _full(rel):
    return os.path.join(ROOT, rel.replace("/", os.sep))


# ── 正文抽取 ────────────────────────────────────────────────

class _HtmlText(HTMLParser):
    """html → 可搜文本。script / style 整块跳过，标签剥掉，空白折叠。"""
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, d):
        if not self._skip and d.strip():
            self.buf.append(d)


def _extract_md(full):
    try:
        size = os.path.getsize(full)
        with open(full, encoding="utf-8", errors="replace") as f:
            raw = f.read(MD_MAX_BYTES)
    except OSError as e:
        return "", None, f"读不了：{e}"
    note = "超长截断" if size > MD_MAX_BYTES else ""
    # md 的「正文」就是原文整份（标签块里的关键词搜到也是对的），所以 原文 那一列
    # 不再存第二份。**外部覆写留档改成读 正文**——两列本来一模一样，存两遍白白
    # 占了 24 MB，正好是 50 MB 验收线的一半。原文 这一列留着给以后可能出现的
    # 「正文是加工过、留档要原样」的类型用，md 一律写 None。
    return raw, None, note


# 连着 2 KB 以上的 base64 字符＝内嵌的图片、字体或数据块。那不是能被搜的字，
# 却能把一份 html 的正文额度整个占满，把真正的内容挤到截断线外面去。
_B64_RUN = re.compile(r"[A-Za-z0-9+/=_-]{2048,}")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _extract_html(full):
    """html → 可搜文本。

    · **<title> 单拎到最前面。** 有些页面正文极长，或整份内容裹在 <template> 里，
      标题会被截断或丢掉。按标题搜是这类文件最主要的找法，必须保住。
    · **<template> 内容和超长 base64 块丢掉。** 前者是渲染前的壳，后者是二进制，
      都占额度、没有检索价值。
    """
    try:
        size = os.path.getsize(full)
        with open(full, encoding="utf-8", errors="replace") as f:
            raw = f.read(HTML_READ_BYTES)
    except OSError as e:
        return "", None, f"读不了：{e}"
    p = _HtmlText()                              # SKIP 里已经含 template
    try:
        p.feed(raw)
    except Exception:
        pass
    text = re.sub(r"[ \t　]+", " ", _B64_RUN.sub(" ", "\n".join(p.buf)))
    note = "超长截断" if size > HTML_READ_BYTES or len(text) > HTML_TEXT_CHARS else ""
    body = text[:HTML_TEXT_CHARS]
    m = _TITLE_RE.search(raw)
    title = (re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
             if m else "")
    if title and not body.startswith(title):
        body = (title + "\n" + body)[:HTML_TEXT_CHARS]
    return body, None, note


# ── 表格解析（xlsx / csv → 前几行纯文本）─────────────────────
#
# 2026-08-25 从 sheet_read.py 搬进来。那个模块随「门户里转表」一起退役，但索引
# 还要认得出表里的字，解析这一层没有别的家可回。只用标准库（Apple 自带的
# /usr/bin/python3 就能跑），不引 openpyxl / pandas——这套工具的前提是不装第三方包。
#
# 踩过的三个坑，代码里都对着处理了：
# 1. 单元格有三种写法：sharedStrings（t="s"）、inlineStr（<is><t>）、纯数字。
#    千牛导出的天猫售后明细整张表都是 inlineStr，只认 <v> 会读成一片空白。
# 2. 日期在 xlsx 里是序列号，要按 styles.xml 里的 numFmt 回推，
#    不然「2026-08-20」被索引成 46254，按日期怎么搜都搜不到。
#    中文 Excel 的日期格式号是 27～36、50～58 那几段。
# 3. 有些 xlsx 的表体是图片不是数据（小排灯那份 16 MB 只有 411 个文字单元格，
#    主体是 147 张嵌入图）。这种抽出来几乎是空的，所以要在 备注 里说一声，
#    不然搜不到会以为是索引坏了。

_XNS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XRNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
# 内置日期格式号。14～22 是常规日期时间，27～36 和 50～58 是中日韩那几套
_DATE_FMT_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))
_QUOTED = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')


def _is_date_fmt(code: str) -> bool:
    """自定义格式码里有没有年月日时分。先把引号、方括号、转义段去掉，
    不然 [$-804] 这种区域标记里的 d 会被当成「日」。"""
    if not code:
        return False
    return bool(re.search(r"[ymdhs]", _QUOTED.sub("", code), re.I))


def _col_index(ref: str) -> int:
    """A1 → 0，BA12 → 52。取不出来返回 -1。"""
    n = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - 64)
        elif "a" <= ch <= "z":
            n = n * 26 + (ord(ch) - 96)
        else:
            break
    return n - 1


def _num(text: str) -> str:
    """数字去掉浮点噪声：3.0 → 3，0.30000000000000004 → 0.3。"""
    try:
        f = float(text)
    except (TypeError, ValueError):
        return text or ""
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(round(f, 10)).rstrip("0").rstrip(".")


def _serial_to_date(text: str, base1904: bool) -> str:
    try:
        v = float(text)
    except (TypeError, ValueError):
        return text or ""
    epoch = datetime(1904, 1, 1) if base1904 else datetime(1899, 12, 30)
    try:
        dt = epoch + timedelta(days=v)
    except OverflowError:
        return _num(text)
    if v < 1:                                   # 只有时间没有日期
        return dt.strftime("%H:%M:%S")
    if abs(v - int(v)) < 1e-9:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")


def _sheet_list(z):
    """按 workbook.xml 里的顺序返回 [(表名, zip 内路径)]。
    r:id 要过一遍 rels 才知道对应哪个 sheetN.xml，序号跟文件名不保证一致。"""
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        wb = None
    rels = {}
    try:
        for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
            rels[r.get("Id")] = r.get("Target") or ""
    except (KeyError, ET.ParseError):
        pass
    out = []
    if wb is not None:
        for sh in wb.iter(_XNS + "sheet"):
            tgt = (rels.get(sh.get(_XRNS + "id") or "", "")).lstrip("/")
            path = tgt if tgt.startswith("xl/") else ("xl/" + tgt if tgt else "")
            if path in z.namelist():
                out.append((sh.get("name") or f"表{len(out) + 1}", path))
    if not out:                                  # workbook 读不了就按文件名兜底
        for n in sorted(x for x in z.namelist()
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", x)):
            out.append((f"表{len(out) + 1}", n))
    return out


def _date1904(z) -> bool:
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        return False
    pr = wb.find(_XNS + "workbookPr")
    return bool(pr is not None and pr.get("date1904") in ("1", "true"))


def _date_styles(z):
    """cellXfs 里第 i 个格式是不是日期。单元格的 s="12" 就是往这张表里查。"""
    try:
        st = ET.fromstring(z.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return []
    custom = {}
    for nf in st.iter(_XNS + "numFmt"):
        try:
            custom[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
        except (TypeError, ValueError):
            continue
    out = []
    xfs = st.find(_XNS + "cellXfs")
    if xfs is None:
        return out
    for xf in xfs.findall(_XNS + "xf"):
        try:
            fid = int(xf.get("numFmtId") or 0)
        except ValueError:
            fid = 0
        out.append(fid in _DATE_FMT_IDS or _is_date_fmt(custom.get(fid, "")))
    return out


def _shared_strings(z, need):
    """只把用到的那几条共享字符串取出来。整份 sharedStrings 可能有几万条，
    前 200 行用不到那么多。"""
    if not need or "xl/sharedStrings.xml" not in z.namelist():
        return {}
    out, i = {}, 0
    with z.open("xl/sharedStrings.xml") as f:
        for _, el in ET.iterparse(f, ("end",)):
            if el.tag != _XNS + "si":
                continue
            if i in need:
                out[i] = "".join(t.text or "" for t in el.iter(_XNS + "t"))
            i += 1
            el.clear()
            if len(out) >= len(need):
                break
    return out


def _read_xlsx(full, sheet, max_rows, max_cols):
    try:
        z = zipfile.ZipFile(full)
    except (zipfile.BadZipFile, OSError) as e:
        return {"ok": False, "错误": f"打不开这份 xlsx：{e}"}
    with z:
        sheets = _sheet_list(z)
        if not sheets:
            return {"ok": False, "错误": "这份文件里没有工作表"}
        sheet = max(0, min(sheet, len(sheets) - 1))
        path = sheets[sheet][1]
        styles = _date_styles(z)
        b1904 = _date1904(z)

        total_rows = 0
        rows, need = [], set()
        try:
            with z.open(path) as f:
                for _, el in ET.iterparse(f, ("end",)):
                    if el.tag == _XNS + "dimension":
                        m = re.search(r"(\d+)$", el.get("ref") or "")
                        if m:
                            total_rows = int(m.group(1))
                        continue
                    if el.tag != _XNS + "row":
                        continue
                    cells = []
                    for c in el.iter(_XNS + "c"):
                        ci = _col_index(c.get("r") or "")
                        if ci < 0 or ci >= max_cols:
                            continue
                        t = c.get("t")
                        if t == "inlineStr":
                            is_ = c.find(_XNS + "is")
                            val = ("".join(x.text or "" for x in is_.iter(_XNS + "t"))
                                   if is_ is not None else "")
                            cells.append((ci, "v", val))
                            continue
                        v = c.find(_XNS + "v")
                        if v is None or v.text is None:
                            continue
                        if t == "s":
                            try:
                                k = int(v.text)
                            except ValueError:
                                continue
                            need.add(k)
                            cells.append((ci, "s", k))
                        elif t in ("str", "e"):
                            cells.append((ci, "v", v.text))
                        elif t == "b":
                            cells.append((ci, "v", "TRUE" if v.text == "1" else "FALSE"))
                        else:
                            try:
                                si = int(c.get("s") or 0)
                            except ValueError:
                                si = 0
                            is_date = si < len(styles) and styles[si]
                            cells.append((ci, "v", _serial_to_date(v.text, b1904)
                                          if is_date else _num(v.text)))
                    rows.append(cells)
                    el.clear()
                    if len(rows) > max_rows:      # 多读一行探边，下面再丢掉
                        break
        except (ET.ParseError, KeyError, OSError) as e:
            return {"ok": False, "错误": f"这份 xlsx 解析不了：{e}"}

        more = len(rows) > max_rows
        rows = rows[:max_rows]
        sst = _shared_strings(z, need)
        pics = sum(1 for n in z.namelist()
                   if n.startswith("xl/media/") or n.startswith("xl/charts/chart"))

    width = min(max((ci for cells in rows for ci, _, _ in cells), default=-1) + 1,
                max_cols)
    table = []
    for cells in rows:
        line = [""] * width
        for ci, kind, val in cells:
            if ci < width:
                line[ci] = sst.get(val, "") if kind == "s" else str(val)
        table.append(line)
    return {"ok": True, "表名": [s[0] for s in sheets], "当前表": sheet, "行": table,
            "截断": more or total_rows > len(table), "图数": pics}


def _sniff_encoding(head: bytes) -> str:
    """库里的 csv 有 utf-8 也有 GBK（平台后台导出的多是 GBK）。
    用增量解码器试，免得被截断在多字节字符中间时误判。"""
    for enc in ("utf-8-sig", "gbk"):
        dec = codecs.getincrementaldecoder(enc)(errors="strict")
        try:
            dec.decode(head)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


def _read_csv(full, max_rows, max_cols):
    try:
        with open(full, "rb") as f:
            head = f.read(64 * 1024)
    except OSError as e:
        return {"ok": False, "错误": f"读不了：{e}"}
    enc = _sniff_encoding(head)
    try:
        delim = csv.Sniffer().sniff(head.decode(enc, errors="replace")[:4096],
                                    delimiters=",\t;|").delimiter
    except csv.Error:
        delim = "\t" if full.lower().endswith(".tsv") else ","
    rows = []
    try:
        with open(full, "r", encoding=enc, errors="replace", newline="") as f:
            for r in csv.reader(f, delimiter=delim):
                rows.append([str(x) for x in r[:max_cols]])
                if len(rows) >= max_rows:
                    break
            more = next(f, None) is not None
    except (OSError, csv.Error) as e:
        return {"ok": False, "错误": f"这份 csv 解析不了：{e}"}
    return {"ok": True, "表名": [], "当前表": 0, "行": rows,
            "截断": more, "图数": 0}


def read_table(full, sheet=0, max_rows=TBL_MAX_ROWS, max_cols=TBL_MAX_COLS):
    """读一份表的前几行。返回 dict，失败时 ok=False 带一句人看得懂的错误。"""
    low = full.lower()
    if low.endswith(".xls"):
        return {"ok": False,
                "错误": "老式 .xls 不是 zip 包，标准库读不了"}
    if low.endswith((".xlsx", ".xlsm")):
        sheet = int(sheet or 0)
        out = _read_xlsx(full, sheet, max_rows, max_cols)
        # 从飞书这类地方导出来的工作簿，第一张表常常是空的 Sheet1，数据在后面。
        # 没指定看哪张时，跳到第一张有数据的，别抽出来是一片空白。
        if sheet == 0 and out.get("ok") and not out["行"] and len(out.get("表名") or []) > 1:
            for i in range(1, len(out["表名"])):
                nxt = _read_xlsx(full, i, max_rows, max_cols)
                if nxt.get("ok") and nxt["行"]:
                    out = nxt
                    break
    elif low.endswith((".csv", ".tsv")):
        out = _read_csv(full, max_rows, max_cols)
    else:
        return {"ok": False, "错误": "这个格式没有表格解析器"}
    if out.get("ok"):                            # 超长的格子截一下
        for row in out["行"]:
            for i, c in enumerate(row):
                if len(c) > TBL_MAX_CHARS:
                    row[i] = c[:TBL_MAX_CHARS] + "…"
    return out


def _extract_sheet(full, kind):
    """xlsx / csv 的文字单元格拼成行，只取表头和前 TBL_MAX_ROWS 行。"""
    texts, note = [], ""
    try:
        first = read_table(full)
    except Exception as e:
        return "", None, f"解析不了：{e}"
    if not first.get("ok"):
        return "", None, first.get("错误", "解析不了")
    tabs = first.get("表名") or []
    outs = [first]
    if kind == "xlsx" and len(tabs) > 1:
        for i in range(1, min(len(tabs), SHEET_MAX_SHEETS)):
            if i == first.get("当前表"):
                continue
            try:
                nxt = read_table(full, i)
            except Exception:
                continue
            if nxt.get("ok"):
                outs.append(nxt)
    n = 0
    for o in outs:
        for row in o.get("行") or []:
            line = " ".join(c for c in row if c)
            if line:
                texts.append(line)
                n += len(line)
        if n > SHEET_TEXT_CHARS:
            note = "超长截断"
            break
    if first.get("截断"):
        note = note or f"只索引了表头和前 {TBL_MAX_ROWS} 行"
    body = "\n".join(texts)[:SHEET_TEXT_CHARS]
    # 文字少、图又多的，是「表体本来就是图」那一类。不说一声会以为索引漏了这份
    if first.get("图数") and len(body) < 400:
        note = f"表体多半是图（{first['图数']} 张），文字抽不出来"
    return body, None, note


# pdf 走系统 PDFKit（WKWebView 渲染 pdf 用的同一个框架），osascript 调，零依赖。
# Spotlight 那条路（mdls kMDItemTextContent）实测全库 112 份返回全空，不能用。
_PDF_JS = r"""
ObjC.import('Quartz');
function run(argv){
  const cap = %d;
  const out = {};
  for (const p of argv){
    try{
      const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(p));
      if (doc.isNil()){ out[p] = null; continue; }
      const s = doc.string;
      out[p] = s.isNil() ? '' : s.js.slice(0, cap);
    }catch(e){ out[p] = null; }
  }
  return JSON.stringify(out);
}
""" % PDF_TEXT_CHARS


def pdf_texts(fulls):
    """一批 pdf → {绝对路径: 文本}。文本为 None＝打不开，''＝没有文本层（扫描件）。"""
    out = {}
    for i in range(0, len(fulls), PDF_BATCH):
        chunk = fulls[i:i + PDF_BATCH]
        try:
            r = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", _PDF_JS] + chunk,
                capture_output=True, timeout=PDF_TIMEOUT)
            got = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        except Exception:
            got = {}
        for p in chunk:
            out[p] = got.get(p)
    return out


# ── 链接图：md 里指向库内的链接 ─────────────────────────────

_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_WIKI_RE = re.compile(r"\[\[([^\[\]|\n]{1,80})\]\]")
_CODE_FENCE = re.compile(r"^\s*```.*?^\s*```\s*?$", re.S | re.M)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def _norm_target(src_rel, u):
    """把 md 里写的相对地址解成库内规范路径。带 scheme 的、锚点、跳出库根的丢掉。"""
    u = (u or "").strip()
    if not u or re.match(r"^[a-z][a-z0-9+.\-]*:", u, re.I) or u.startswith(("#", "//")):
        return None
    u = u.split("#")[0].split("?")[0]
    if not u.lower().endswith((".md", ".html")):
        return None
    try:
        u = re.sub(r"%[0-9A-Fa-f]{2}",
                   lambda m: bytes.fromhex(m.group(0)[1:]).decode("utf-8", "replace"), u)
    except Exception:
        pass
    base = "" if u.startswith("/") else os.path.dirname(src_rel)
    parts = []
    for seg in (base + "/" + u.lstrip("/")).split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            if not parts:
                return None                        # 越出库根
            parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts) or None


def extract_links(rel, body):
    """返回 [(类型, 目标, 文本)]。类型：路径＝指到具体文件；题名＝[[..]] 这种按名找。
    代码块和行内代码里的链接是举例，不算引用。"""
    body = _CODE_FENCE.sub("", body or "")
    body = _INLINE_CODE.sub("", body)
    out, seen = [], set()
    for m in _LINK_RE.finditer(body):
        tgt = _norm_target(rel, m.group(2))
        if tgt and tgt != rel and ("路径", tgt) not in seen:
            seen.add(("路径", tgt))
            out.append(("路径", tgt, m.group(1)[:80]))
    for m in _WIKI_RE.finditer(body):
        name = m.group(1).strip()
        if name and ("题名", name) not in seen:
            seen.add(("题名", name))
            out.append(("题名", name, name))
    return out


# ── 数据库 ──────────────────────────────────────────────────

def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA mmap_size=536870912")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS 文档(
      路径 TEXT PRIMARY KEY, 类型 TEXT, mtime REAL, 大小 INTEGER,
      正文 TEXT DEFAULT '', 原文 TEXT, 备注 TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS 链接(
      源 TEXT, 类型 TEXT, 目标 TEXT, 文本 TEXT);
    CREATE INDEX IF NOT EXISTS 链接_目标 ON 链接(目标);
    CREATE INDEX IF NOT EXISTS 链接_源 ON 链接(源);
    CREATE TABLE IF NOT EXISTS 元(键 TEXT PRIMARY KEY, 值 TEXT);
    """)
    return con


def meta_get(con, k, dflt=""):
    row = con.execute("SELECT 值 FROM 元 WHERE 键=?", (k,)).fetchone()
    return row[0] if row else dflt


def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO 元(键,值) VALUES(?,?)", (k, str(v)))


# ── 留档（跟门户编辑备份同目录同规则） ───────────────────────

def _flat(rel):
    return re.sub(r"[^\w.-]+", "_", rel).strip("_")[:120]


def archive_text(rel, old_text):
    """把一份 md 改动前的内容写进 .amnote/backups/。返回备份文件名，写不了返回 ''。"""
    if not old_text:
        return ""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        flat = _flat(rel)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        name = f"{flat}__{stamp}.bak"
        with open(os.path.join(BACKUP_DIR, name), "w", encoding="utf-8") as f:
            f.write(old_text)
    except OSError:
        return ""
    # 同一份文件只留最近 BACKUP_KEEP 版（跟门户编辑那套同额同名，互相算在一起）
    try:
        olds = sorted(fn for fn in os.listdir(BACKUP_DIR)
                      if fn.startswith(flat + "__") and fn.endswith(".bak"))
        for fn in olds[:-BACKUP_KEEP]:
            os.remove(os.path.join(BACKUP_DIR, fn))
    except OSError:
        pass
    _prune_backups()
    return name


def _prune_backups():
    """备份目录总大小超上限就从最旧的删起。Agent 批量改几百份时别让它无限长。"""
    try:
        fns = [(fn, os.path.getmtime(os.path.join(BACKUP_DIR, fn)),
                os.path.getsize(os.path.join(BACKUP_DIR, fn)))
               for fn in os.listdir(BACKUP_DIR) if fn.endswith(".bak")]
    except OSError:
        return
    total = sum(s for _, _, s in fns)
    cap = BACKUP_TOTAL_MB * 1024 * 1024
    if total <= cap:
        return
    for fn, _, s in sorted(fns, key=lambda x: x[1]):
        try:
            os.remove(os.path.join(BACKUP_DIR, fn))
            total -= s
        except OSError:
            pass
        if total <= cap:
            break


def archive_read(name):
    """按文件名读一份留档。只认 .amnote/backups/ 里的 .bak 文件名，路径穿越进不来。"""
    if (not name or "/" in name or os.sep in name or ".." in name
            or not name.endswith(".bak")):
        return None
    p = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# ── 门户写入活表：分「门户改的」和「外部改的」 ───────────────
#
# 这张表是流水里「来源」那一列的全部依据，也决定要不要留档（门户自己有留档，
# 外部改动才需要 sync 这边补一版）。
#
# **判定必须在判定当刻查这张活表，不能吃线程启动时的快照。** v20 的写法是
# portal_server 起后台线程时 `dict(_portal_writes)` 拷一份带进来，于是：一次保存
# 记进表 → 起线程 B；线程 A 还在跑，B 只登记「待重跑」就退了；A 跑完补跑的那趟
# 用的是 A 出发时的旧快照，里面没有刚记的那笔，于是把门户自己的保存判成「外部」，
# 白留一版档、流水上也记错来源。停笔 2 秒就落盘的自动保存上线之后，这个误记会从
# 偶发变成常态，所以 v3 把表收进来、判定当刻加锁查。
#
# 查中了就**消费掉**：一笔保存只该被认领一次。同一趟同步里 src_of 会被问两遍
# （留档一次、记流水一次），所以 _sync_once 在一开始就把这一批的判定结果算好存下，
# 后面全查那份结果，不重复消费。

_pw_lock = threading.Lock()
_portal_writes = {}                   # {相对路径: [写入时间, ...]}
PW_WINDOW = 120                       # 保存时间和文件 mtime 差这么多秒内算同一笔
PW_FILES = 500                        # 活表最多盯这么多份文件


def _pw_prune(now):
    """掐掉过了窗口的旧笔，顺带清空的键。调用方必须已经拿着锁。"""
    for k in list(_portal_writes):
        keep = [w for w in _portal_writes[k] if now - w < PW_WINDOW]
        if keep:
            _portal_writes[k] = keep
        else:
            _portal_writes.pop(k, None)


def note_portal_write(rel):
    """门户里每写成功一笔（/__save）就记一条。portal_server 落盘前就叫。

    **一份文件挂一串时间，不是一个格子。** 停笔 2 秒落一次盘，一趟同步跑着的时候
    可能已经又存了两回；同步这边是拿两份快照比 (mtime, 大小)，同一份文件因此
    可能被前后两趟同步各看见一次改动。一个格子只够认领一次，第二次就落空、
    判成「外部」，白留一版档还在流水上多记一行——这正是 20260829 沙箱里
    「3 次门户保存出来 1 条门户 ＋ 1 条外部」的成因。
    """
    if not rel:
        return
    now = time.time()
    with _pw_lock:
        _portal_writes.setdefault(rel, []).append(now)
        _pw_prune(now)
        if len(_portal_writes) > PW_FILES:        # 兜底，正常到不了
            for k in list(_portal_writes)[:len(_portal_writes) - PW_FILES]:
                _portal_writes.pop(k, None)


def take_portal_write(rel, mtime, window=PW_WINDOW):
    """这份文件这一次改动是不是门户自己写的。认领哪一笔就消费哪一笔。

    按「离这次 mtime 最近」挑，不是先进先出：同步看见的先后跟保存的先后
    不保证一致，挑最近的那笔才对得上号。
    """
    now = time.time()
    with _pw_lock:
        _pw_prune(now)
        lst = _portal_writes.get(rel)
        if not lst:
            return False
        i = min(range(len(lst)), key=lambda j: abs(mtime - lst[j]))
        if abs(mtime - lst[i]) >= window:
            return False
        lst.pop(i)
        if not lst:
            _portal_writes.pop(rel, None)
        return True


# ── 变更流水 ────────────────────────────────────────────────

JOURNAL_MERGE_GAP = 600               # 门户连续保存的合并窗口（10 分钟）


def _journal_append(entries):
    if not entries:
        return
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _within(a, b, gap):
    """两个 "%Y-%m-%d %H:%M:%S" 时间戳相差不到 gap 秒。读不出来算不相近。"""
    try:
        ta = datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        tb = datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return abs((tb - ta).total_seconds()) < gap


def _journal_add(entries, seq):
    """把这一批事件落进流水，返回 (用到的最大序号, 新增了几行)。

    **门户的连续保存合并进上一条，不新增行。** 自动保存是停笔 2 秒落一次盘，
    照实记的话改一段话就是几十行「修改」，把「昨天动了哪些文件」冲成一片心跳。
    合并要同时满足四条：同一份文件、这一条是门户来的「修改」、流水里这份文件
    的上一条也是门户来的「修改」、两条间隔不足 JOURNAL_MERGE_GAP 秒。

    合并只改上一条的 时间 和 大小KB，**序号不动**——/__changes?since= 是按序号
    取增量的，改序号会让客户端把同一件事再收一遍。**外部改动一条都不合**，
    逐条照记：那才是「别人动了我的文件」，一次都不能漏。
    """
    if not entries:
        return seq, 0

    def can_merge(e):
        return e.get("事件") == "修改" and e.get("来源") == "门户"

    if not any(can_merge(e) for e in entries):        # 快路：不用读整份流水
        out = []
        for e in entries:
            seq += 1
            out.append({"序号": seq, **e})
        _journal_append(out)
        return seq, len(out)

    # 要就地改上一条，只能把整份读进来。流水是 1 MB 上下的小文件，
    # 而这条路只在门户连续保存时才走
    recs = []
    try:
        with open(JOURNAL, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    recs.append(line)                 # 读不懂的原样留着，不丢
    except OSError:
        recs = []

    def last_of(rel):
        for i in range(len(recs) - 1, -1, -1):
            if isinstance(recs[i], dict) and recs[i].get("路径") == rel:
                return i
        return -1

    add, dirty = [], False
    for e in entries:
        i = last_of(e["路径"]) if can_merge(e) else -1
        prev = recs[i] if i >= 0 else None
        if (prev and prev.get("来源") == "门户" and prev.get("事件") == "修改"
                and _within(prev.get("时间"), e.get("时间"), JOURNAL_MERGE_GAP)):
            prev["时间"] = e["时间"]
            prev["大小KB"] = e.get("大小KB", prev.get("大小KB"))
            if e.get("留档"):                          # 这一轮真留了档就记上
                prev["留档"] = e["留档"]
            dirty = True
            continue
        seq += 1
        add.append({"序号": seq, **e})

    if not dirty:
        _journal_append(add)
        return seq, len(add)

    tmp = JOURNAL + ".tmp"                            # 先写临时文件再改名
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for r in recs:
                f.write((json.dumps(r, ensure_ascii=False)
                         if isinstance(r, dict) else r) + "\n")
            for e in add:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, JOURNAL)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        _journal_append(add)                          # 重写失败退回追加，别丢这一批
    return seq, len(add)


def journal_read(after_seq=0, limit=500):
    """读流水，只回序号大于 after_seq 的，最多 limit 条（从新往旧截）。"""
    out = []
    try:
        with open(JOURNAL, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("序号", 0) > after_seq:
                    out.append(e)
    except OSError:
        return []
    return out[-limit:]


# ── 同步：抽正文＋记流水＋留档，一趟做完 ─────────────────────

def sync(log=None):
    """跟磁盘对一遍。

    「门户改的」和「外部改的」怎么分，见上面 note_portal_write 那一段——
    v3 起判定在 _sync_once 里当刻查活表，**不再由调用方传快照进来**
    （那个参数就是 v20 那个误记 bug 的来源，一起删了）。

    并发：同一时刻只跑一趟。跑着的时候又被叫，登记一次待重跑，跑完自动补。"""
    if not _sync_lock.acquire(blocking=False):
        _state["待重跑"] = True
        return {"ok": False, "说明": "已有一趟在跑，跑完会自动补一轮"}
    _state["运行中"] = True
    try:
        r = _sync_once(log or (lambda *a: None))
    finally:
        _state["运行中"] = False
        _sync_lock.release()
    if _state["待重跑"]:
        _state["待重跑"] = False
        return sync(log)
    return r


def _sync_once(log):
    t0 = time.time()
    con = connect()
    cur = walk_files()
    old = {r: (m, s, k) for r, m, s, k in
           con.execute("SELECT 路径,mtime,大小,类型 FROM 文档")}

    first_run = not old
    seq = int(meta_get(con, "流水号", "0") or 0)
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    events = []

    added = [r for r in cur if r not in old]
    changed = [r for r in cur if r in old
               and (cur[r][0], cur[r][1]) != (old[r][0], old[r][1])]
    removed = [r for r in old if r not in cur]

    # 门户／外部**只判一次，判完存下来**。take_portal_write 查中即消费，
    # 而下面留档和记流水会各问一遍同一份文件；每问一次就消费一次的话，
    # 第二问必然落空，同一笔改动会一半算门户一半算外部
    src = {r: ("门户" if take_portal_write(r, cur[r][0]) else "外部")
           for r in added + changed}

    def src_of(rel):
        return src.get(rel, "外部")

    # 留档要赶在重抽之前：改动前的原文还躺在 db 里，重抽一跑就被新内容盖掉了
    for r in changed:
        if cur[r][2] != "md":
            continue
        row = con.execute("SELECT coalesce(原文,正文) FROM 文档 WHERE 路径=?", (r,)).fetchone()
        bak = archive_text(r, row[0] if row else "") if src_of(r) == "外部" else ""
        if bak:
            events.append({"路径": r, "留档": bak})
    for r in removed:
        if old[r][2] != "md":
            continue
        row = con.execute("SELECT coalesce(原文,正文) FROM 文档 WHERE 路径=?", (r,)).fetchone()
        bak = archive_text(r, row[0] if row else "")
        if bak:
            events.append({"路径": r, "留档": bak})
    baks = {e["路径"]: e["留档"] for e in events}
    events = []
    n_rows = 0                                   # 这一趟真往流水里新增了几行

    if not first_run:
        # 序号在 _journal_add 里发：合并进上一条的那些不占号，先攒着不编号
        for r in sorted(added):
            events.append({"时间": now_iso, "事件": "新增", "路径": r,
                           "类型": cur[r][2], "来源": src_of(r),
                           "大小KB": round(cur[r][1] / 1024, 1)})
        for r in sorted(changed):
            events.append({"时间": now_iso, "事件": "修改", "路径": r,
                           "类型": cur[r][2], "来源": src_of(r),
                           "大小KB": round(cur[r][1] / 1024, 1),
                           "留档": baks.get(r, "")})
        for r in sorted(removed):
            events.append({"时间": now_iso, "事件": "删除", "路径": r,
                           "类型": old[r][2], "来源": "外部",
                           "留档": baks.get(r, "")})
        seq, n_rows = _journal_add(events, seq)
        meta_set(con, "流水号", seq)
    else:
        meta_set(con, "基线时间", now_iso)
        meta_set(con, "流水号", seq)

    # 抽正文。pdf 攒一批交给 osascript，其余就地抽
    todo = added + changed
    pdf_todo = []
    for r in todo:
        kind = cur[r][2]
        full = _full(r)
        if kind == "pdf":
            pdf_todo.append(r)
            continue
        if kind == "md":
            body, raw, note = _extract_md(full)
        elif kind == "html":
            body, raw, note = _extract_html(full)
        elif kind in ("xlsx", "csv"):
            body, raw, note = _extract_sheet(full, kind)
        else:                                      # xls / docx / pptx：只记不抽
            body, raw, note = "", None, "没有抽取器"
        con.execute("INSERT OR REPLACE INTO 文档(路径,类型,mtime,大小,正文,原文,备注) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (r, kind, cur[r][0], cur[r][1], body, raw, note))
        if kind == "md":
            con.execute("DELETE FROM 链接 WHERE 源=?", (r,))
            con.executemany("INSERT INTO 链接(源,类型,目标,文本) VALUES(?,?,?,?)",
                            [(r, t, g, x) for t, g, x in extract_links(r, body)])
    for r in removed:
        con.execute("DELETE FROM 文档 WHERE 路径=?", (r,))
        con.execute("DELETE FROM 链接 WHERE 源=?", (r,))
    con.commit()

    if pdf_todo:
        log(f"抽 {len(pdf_todo)} 份 pdf 的文本层")
        got = pdf_texts([_full(r) for r in pdf_todo])
        for r in pdf_todo:
            t = got.get(_full(r))
            body = t or ""
            note = "读不了" if t is None else ("无文本层" if not t.strip() else "")
            con.execute("INSERT OR REPLACE INTO 文档(路径,类型,mtime,大小,正文,原文,备注) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (r, "pdf", cur[r][0], cur[r][1], body, None, note))
        con.commit()

    meta_set(con, "上次同步", now_iso)
    con.commit()
    n_docs = con.execute("SELECT COUNT(*) FROM 文档").fetchone()[0]
    con.close()
    r = {"ok": True, "收录": n_docs, "新增": len(added), "修改": len(changed),
         "删除": len(removed), "流水": n_rows, "耗时秒": round(time.time() - t0, 2),
         "首次建库": first_run}
    log(f"全文同步：{r}")
    return r


def compact(log=None):
    """按当前口径把索引瘦一遍：重抽表格正文 → 清掉 md 的重复副本 → VACUUM。

    为什么要单开这一条：sync 靠 (mtime, 大小) 判断要不要重抽，改的是截断参数、
    文件本身没动，光跑 --sync 一份都不会重抽。**这条只改 正文 / 原文 / 备注 三列，
    mtime 和 大小 原样不动**——动了会被下一次 sync 当成「文件改了」，
    往 changes.jsonl 里灌一千多条假的「新增／修改」，那份流水是遥测，不能脏。

    VACUUM 是必须的：腾出来的空间只是在 db 里留成空页，文件大小不会自己降下来。
    """
    log = log or (lambda *a: None)
    t0 = time.time()
    con = connect()
    rows = con.execute("SELECT 路径,类型 FROM 文档 "
                       "WHERE 类型 IN ('xlsx','csv','xls')").fetchall()
    n_ok = n_bad = 0
    for i, (rel, kind) in enumerate(rows):
        full = _full(rel)
        if not os.path.isfile(full):
            continue
        if kind == "xls":                        # 没有抽取器，只把旧正文清干净
            body, note = "", "没有抽取器"
        else:
            body, _, note = _extract_sheet(full, kind)
        con.execute("UPDATE 文档 SET 正文=?, 备注=? WHERE 路径=?", (body, note, rel))
        if body:
            n_ok += 1
        else:
            n_bad += 1
        if (i + 1) % 200 == 0:
            con.commit()
            log(f"  重抽 {i + 1}/{len(rows)}")
    con.commit()
    # md 的 原文 和 正文 从来是同一份（见 _extract_md），历史行里存了两遍。
    # 只清一模一样的那些，将来若有「正文加工过、留档要原样」的类型不会被误伤。
    dup = con.execute("UPDATE 文档 SET 原文=NULL "
                      "WHERE 原文 IS NOT NULL AND 原文=正文").rowcount
    con.commit()
    before = os.path.getsize(DB_PATH)
    con.execute("VACUUM")
    con.close()
    r = {"ok": True, "重抽": len(rows), "有正文": n_ok, "空的": n_bad,
         "去重复副本": dup,
         "库MB": {"前": round(before / 1048576, 1),
                  "后": round(os.path.getsize(DB_PATH) / 1048576, 1)},
         "耗时秒": round(time.time() - t0, 1)}
    log(f"索引瘦身：{r}")
    return r


# ── 搜索 ────────────────────────────────────────────────────

def _like_esc(t):
    return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def snippets(body, terms, per_term=2, radius=42):
    """正文里每个词取前几处命中，带前后文。返回 [{前,中,后}]。"""
    low = body.lower()
    out, taken = [], []
    for t in terms:
        tl = t.lower()
        pos, n = 0, 0
        while n < per_term:
            i = low.find(tl, pos)
            if i < 0:
                break
            if any(abs(i - a) < radius for a in taken):
                pos = i + len(tl)
                continue
            taken.append(i)
            a, b = max(0, i - radius), min(len(body), i + len(tl) + radius)
            out.append({
                "前": ("…" if a > 0 else "") + body[a:i].replace("\n", " "),
                "中": body[i:i + len(tl)],
                "后": body[i + len(tl):b].replace("\n", " ") + ("…" if b < len(body) else ""),
            })
            pos = i + len(tl)
            n += 1
    return out[:4]


def search(q, limit=60):
    """正文 ＋ 文件名子串搜索。多个词（空格隔开）是 AND。

    v21 修平了两处，都是「搜不到自己知道存在的那份文件」这一类问题：

    · **文件名进匹配。** 原来只查 正文 一列，搜 `creative_day` 这种只出现在
      文件名里的词返回 0 条。而库里相当一部分检索意图就是「那份叫什么什么
      的文件在哪」，让它返回空是最没道理的一种空。现在每个词命中 正文 或
      命中 路径 都算命中，AND 的语义不变。
    · **两档排序。** 所有词都出现在文件名里的进第一档，按改动时间倒序：
      文件名打全了，要的就是那一份，不该被一份正文里提了它四十次的长文
      压在下面。其余进第二档，按加权分排——命中次数取对数（第 40 次命中和
      第 4 次的差别，没有 4 次和 1 次那么大）、文件名／目录命中加分、
      再加一点时间新鲜度。

    返回 [{路径, 类型, 命中数, 备注, 片段, 档}]，跟 v20 比只多一个 档 字段。
    命中数 现在是「正文命中 ＋ 文件名命中」：只靠文件名中的那些，
    报 0 次命中会像是坏了。
    """
    terms = [t for t in (q or "").split() if t]
    if not terms:
        return {"ok": True, "状态": index_status(), "结果": []}
    con = connect()
    st = index_status(con)
    where = " AND ".join(
        ["(正文 LIKE ? ESCAPE '\\' OR 路径 LIKE ? ESCAPE '\\')"] * len(terms))
    args = []
    for t in terms:
        pat = "%" + _like_esc(t) + "%"
        args += [pat, pat]
    rows = con.execute(
        f"SELECT 路径,类型,正文,备注,mtime FROM 文档 WHERE {where}", args).fetchall()
    con.close()

    lows = [t.lower() for t in terms]
    now = time.time()
    out = []
    for rel, kind, body, note, mt in rows:
        body = body or ""
        low = body.lower()
        name = rel.rsplit("/", 1)[-1].lower()
        folder = rel.lower().rsplit("/", 1)[0] if "/" in rel else ""
        n_body = sum(low.count(t) for t in lows)
        n_name = sum(name.count(t) for t in lows)
        if all(t in name for t in lows):
            tier, score = 1, (mt or 0)           # 第一档就按改动时间排
        else:
            # 一个月内的新鲜度接近满分，半年前掉到七分之一
            fresh = 1.0 / (1.0 + max(0.0, now - (mt or 0)) / (30 * 86400))
            tier = 2
            score = (math.log1p(n_body)
                     + (2.0 if n_name else 0.0)
                     + (0.6 if any(t in folder for t in lows) else 0.0)
                     + 1.2 * fresh)
        out.append({"路径": rel, "类型": kind, "命中数": n_body + n_name,
                    "备注": note, "档": tier, "_分": score, "_正文": body})
    out.sort(key=lambda x: (x["档"], -x["_分"]))
    # 片段是整份正文扫一遍，只给要返回的那几条算。n 上限从 200 提到 500 之后，
    # 给全部命中都算一遍片段是白扫几百 MB
    out = out[:limit]
    for h in out:
        h["片段"] = snippets(h.pop("_正文"), terms)
        h.pop("_分", None)
    return {"ok": True, "状态": st, "结果": out, "总命中": len(rows)}


def index_status(con=None):
    own = con is None
    if own:
        if not os.path.exists(DB_PATH):
            return {"状态": "未建库", "收录": 0, "上次同步": ""}
        con = connect()
    st = {"状态": "同步中" if _state["运行中"] else "就绪",
          "收录": con.execute("SELECT COUNT(*) FROM 文档").fetchone()[0],
          "无文本层": con.execute(
              "SELECT COUNT(*) FROM 文档 WHERE 备注='无文本层'").fetchone()[0],
          "上次同步": meta_get(con, "上次同步"),
          "流水号": int(meta_get(con, "流水号", "0") or 0)}
    if own:
        con.close()
    return st


# ── 反链与死链 ──────────────────────────────────────────────

def backlinks(rel, title="", stem=""):
    """谁引用了这份：路径直指的，加上 [[题名]] 按标题 / 文件名对上的。"""
    con = connect()
    rows = {r for r, in con.execute(
        "SELECT DISTINCT 源 FROM 链接 WHERE 类型='路径' AND 目标=?", (rel,))}
    names = {n.strip().lower() for n in (title, stem) if n and n.strip()}
    if names:
        for src, tgt in con.execute("SELECT 源,目标 FROM 链接 WHERE 类型='题名'"):
            if tgt.strip().lower() in names:
                rows.add(src)
    con.close()
    rows.discard(rel)
    return sorted(rows)


def deadlinks():
    """指到不存在文件的路径链接。[[题名]] 不算——按名找本来就允许晚点再建。"""
    con = connect()
    have = {r for r, in con.execute("SELECT 路径 FROM 文档")}
    out = []
    for src, tgt, txt in con.execute(
            "SELECT 源,目标,文本 FROM 链接 WHERE 类型='路径' ORDER BY 源"):
        if tgt not in have and not os.path.isfile(_full(tgt)):
            out.append({"源": src, "目标": tgt, "文本": txt})
    con.close()
    return out


# ── 命令行 ──────────────────────────────────────────────────

if __name__ == "__main__":
    root, argv = take_root_arg(sys.argv[1:])
    configure(root)
    if "--sync" in argv:
        print(json.dumps(sync(log=print), ensure_ascii=False))
    elif "--compact" in argv:
        print(json.dumps(compact(log=print), ensure_ascii=False))
    elif "--search" in argv:
        i = argv.index("--search")
        q = " ".join(argv[i + 1:])
        r = search(q)
        print(f"命中 {r.get('总命中', 0)} 份（索引{r['状态']['状态']}，"
              f"收录 {r['状态']['收录']}）")
        for h in r["结果"][:15]:
            frag = h["片段"][0] if h["片段"] else {}
            print(f"  {h['命中数']:>3} × {h['路径']}")
            if frag:
                print(f"        …{frag.get('前','')}【{frag.get('中','')}】{frag.get('后','')}")
    elif "--backlinks" in argv:
        i = argv.index("--backlinks")
        rel = " ".join(argv[i + 1:]).strip()
        srcs = backlinks(rel, "", os.path.splitext(os.path.basename(rel))[0])
        print(f"引用 {rel} 的有 {len(srcs)} 份")
        for s in srcs:
            print("  " + s)
    elif "--deadlinks" in argv:
        for d in deadlinks():
            print(f"{d['源']} → {d['目标']}")
    else:
        print(json.dumps(index_status(), ensure_ascii=False))
