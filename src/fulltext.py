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
库根来自 --root 或环境变量 AMNOTE_VAULT，import 之后、用 V() 之前必须
调用一次 configure()（5.7 起挂多个根走 register()，见「笔记本」那一段）。
.amnote 不进索引。

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
    · **5.9 起「文档」表多一列 `骨架`**（v3 的表头那句「schema 没变」到此为止）：
      md 的结构缩影，索引时算一次存下来，首页卡片照它画一张「纸」。老库开库时
      自动 ALTER ＋ 回填一次，**只在可写连接上**（见 _add_skeleton_col）；
      规则在 skeleton_of，跟页面上的 skeletonOf() 是同一套，改要一起改。

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
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))

BACKUP_KEEP = 10                                  # 每份文件留几版，跟编辑备份同额
BACKUP_TOTAL_MB = 500                             # 备份目录总大小上限，超了删最旧的


# ── 笔记本：一个进程挂几个根（5.7）────────────────────────────
#
# 原来这里是五个路径全局（ROOT / CONFIG_PATH / DB_PATH / JOURNAL / BACKUP_DIR）
# 加一个 READONLY，configure() 一次写死。5.7 起一个进程要同时挂几个文件夹，
# 每个文件夹叫一个**笔记本**，于是换成：
#
#     _vaults   {vid: Vault}   按加入顺序，第一条是「主笔记本」
#     _cur      threading.local 记着「这条线程现在在哪一本里」，缺省 = 主笔记本
#     V()       当前那一本；函数里一律写 V().root / V().db_path …
#
# **没改成类。** 调用点几十处，改成类等于重写整个模块，而这里要的只是
# 「同一套函数换一个根跑一遍」。（5.7 阶段 5：`fulltext.ROOT` 这类老写法的
# 模块级 __getattr__ 兜底删了——全仓库一个调用方都没有，留着只会让人以为
# 还有别的地方在用那几个全局。）
#
# 单库时表里只有一条、当前永远是它：命令行的离线通道
# （configure(root, readonly=True) 之后直接 connect()）一个字都不用动。


class Vault(object):
    """一个笔记本：库根 ＋ 它 .amnote/ 下那几样 ＋ 各自的活状态。

    名字 / 颜色 / 加入时间 / 在不在线由 portal_server 填（认得 notebooks.json 的
    是它），这里只保证字段在、有个说得过去的默认值——两边写同一个对象，
    不各存一份对不上。
    """

    __slots__ = ("vid", "name", "root", "real_root", "color", "added",
                 "config_path", "db_path", "journal", "backup_dir",
                 "readonly", "online", "sync_lock", "state", "rules_cache")

    def __init__(self, vid, root, name="", color="", added="",
                 readonly=False, online=True):
        self.vid = vid
        self.readonly = bool(readonly)
        self.color = color
        self.added = added
        self.online = bool(online)
        # 一把锁、一份同步状态、一份规则缓存**各本一份**：共用的话 A 本在扫
        # B 本就只能排队，而 /__status 的进度也会互相串。
        self.sync_lock = threading.Lock()
        self.state = {"运行中": False, "待重跑": False}
        self.rules_cache = {"mtime": None, "值": None}
        self.point_at(root, name=name)

    def point_at(self, root, name="", wait=10.0):
        """（重新）指向一个库根。「重新定位」走这条：vid 不变，位置换了。

        **跟正在跑的那趟扫描互斥**：db、流水、留档目录全是从下面这几个字段现拼
        出来的，跑到一半换掉的话前半趟写进旧库、后半趟写进新库，两边都不完整。
        所以先等那一趟收尾（最多 `wait` 秒）；真等不到也照换——「重新定位」是
        用户点出来的动作，不能因为一趟大扫描就卡着不给回应。
        （`__init__` 里叫这条时锁是新的，直接就拿到了。）
        """
        got = self.sync_lock.acquire(timeout=wait)
        try:
            return self._point_at(root, name=name)
        finally:
            if got:
                self.sync_lock.release()

    def _point_at(self, root, name=""):
        self.root = os.path.abspath(os.path.expanduser(str(root)))
        self.real_root = os.path.realpath(self.root)
        amdir = os.path.join(self.root, ".amnote")
        self.config_path = os.path.join(amdir, "config.json")
        self.db_path = os.path.join(amdir, "fulltext.db")
        self.journal = os.path.join(amdir, "changes.jsonl")
        self.backup_dir = os.path.join(amdir, "backups")
        self.rules_cache = {"mtime": None, "值": None}
        self.name = name or os.path.basename(self.root.rstrip(os.sep)) or self.root
        return self


_vaults = {}                                      # {vid: Vault}，插入序＝加入序
_cur = threading.local()                          # 这条线程当前钉在哪一本上


def _bind(v):
    """内部：把这条线程钉在一个 Vault **对象**上（None ＝ 回到主笔记本）。"""
    _cur.v = v
    _cur.vid = v.vid if v is not None else None


def V():
    """当前笔记本。没绑过就是主笔记本（＝第一本），一本都没有就抛。

    **认对象，不认 vid。** 只记 vid 的话，一趟扫描跑到一半那一本被「移除」了
    （`unregister` 把它从 `_vaults` 里摘掉），`V()` 就悄悄回落到主笔记本，
    后半趟的流水、留档、正文抽取全写进别人家里。钉住对象之后，被移除的那一本
    照样把自己这一趟跑完（写的是它自己的 `.amnote/`），只是没人再往它身上派新活。
    """
    v = getattr(_cur, "v", None)
    if v is not None:
        return v
    for v in _vaults.values():                    # 插入序的第一条＝主笔记本
        return v
    raise RuntimeError("还没登记任何笔记本：先 configure() 或 register()")


def main_vault():
    """主笔记本（列表第一条）。一本都没有返回 None。"""
    for v in _vaults.values():
        return v
    return None


def vaults():
    """按加入顺序的全部笔记本。"""
    return list(_vaults.values())


def get_vault(vid):
    return _vaults.get(vid)


def set_current(vid):
    """把这条线程绑到某一本上。传 None ＝ 回到主笔记本。"""
    _bind(vid if isinstance(vid, Vault) else _vaults.get(vid))
    return _cur.vid


class use(object):
    """`with use(v):` —— 这一段代码在那一本里跑，出去自动还原。

    后台同步线程和扇出（搜索、树、地图）全靠它：业务函数一个参数都不用加。
    **钉的是对象**：跑到一半那一本被移除了也还写在自己家里（见 `V()`）。
    """

    __slots__ = ("v", "old")

    def __init__(self, v):
        self.v = v if isinstance(v, Vault) else _vaults.get(v)

    def __enter__(self):
        self.old = getattr(_cur, "v", None)
        _bind(self.v)
        return self.v

    def __exit__(self, *exc):
        _bind(self.old)
        return False


def _ensure_dirs(v):
    """建 .amnote/ 和 backups/。只读的笔记本一个目录都不建（见 configure）。"""
    if v.readonly:
        return ""
    for d in (os.path.dirname(v.config_path), v.backup_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            return "建不了 %s：%s" % (d, e)
    return ""


def register(root, vid=None, name="", color="", added="", readonly=False,
             online=None, make_dirs=True):
    """登记一个笔记本，返回 Vault。**不动「当前」，也不动别的本。**

    root 不在（外置盘没插、iCloud 没就绪）时登记成离线：一个目录都不建、
    不扫描，位置留在列表里，等 /__pulse 探到它回来再挂上。
    """
    v = Vault(vid or secrets_token(), root, name=name, color=color,
              added=added, readonly=readonly,
              online=os.path.isdir(os.path.abspath(os.path.expanduser(str(root))))
              if online is None else online)
    if v.online and make_dirs:
        _ensure_dirs(v)
    old = _vaults.get(v.vid)
    if old is not None:                           # 同 vid 重登记：位置不变
        old.point_at(v.root, name=v.name)
        old.online, old.readonly = v.online, v.readonly
        if color:
            old.color = color
        return old
    _vaults[v.vid] = v
    return v


def unregister(vid, wait=10.0):
    """注销一本。文件一个字节都不动，只是这个进程不再挂它。

    先从表里摘掉（下一趟请求立刻找不到它），再等那趟正在跑的扫描收尾——
    钉在 `_cur.v` 上的那条线程写的是它自己的 `.amnote/`，等只是为了让「移除」
    回去的时候库里没有半截活儿在跑。等不到（大库还在扫）也就算了。
    """
    v = _vaults.pop(vid.vid if isinstance(vid, Vault) else vid, None)
    if v is not None and v.sync_lock.acquire(timeout=wait):
        v.sync_lock.release()
    return v


def secrets_token():
    """8 位十六进制的内部 id。**永不出现在路径里**，只在列表和动作参数里用。"""
    return secrets.token_hex(4)


def take_root_args(argv):
    """抽出全部 --root PATH（可重复、可写在任意位置）。返回 (根列表, 剩余参数)。"""
    roots = []
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--root":
            if i + 1 >= len(argv):
                print("请在 --root 后面写下文件夹的路径。", file=sys.stderr)
                sys.exit(1)
            roots.append(argv[i + 1])
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return roots, rest


def take_root_arg(argv):
    """老口径：只取最后一个 --root。返回 (root 或 None, 剩余参数)。"""
    roots, rest = take_root_args(argv)
    return (roots[-1] if roots else None), rest


def configure(root=None, readonly=False):
    """定库根，并把索引 / 流水 / 备份 / 配置指到 {vault}/.amnote/。

    5.7 起它的语义是「把这一个文件夹登记成唯一的、也是当前的笔记本」——
    命令行和只挂一个根的场合照旧一句就够。挂多个根走 register()。

    必须在 import 之后、用 V() 之前调一次。库根来自参数或环境变量
    AMNOTE_VAULT，不再往上找标志文件。没给就在 stderr 说明原因后退出。

    `readonly=True`：只把路径算出来，**一个目录都不建**。命令行在门户没开
    时读上一次的索引走这条——那是别人的笔记文件夹，一条只读命令不该在里面
    留下 `.amnote/`、`backups/` 或者 WAL 的边角料。connect() 跟着看这个开关。
    """
    raw = (root if root is not None else "") or os.environ.get("AMNOTE_VAULT") or ""
    raw = str(raw).strip()
    if not raw:
        print("没有库根。请用 --root 指定一个文件夹，或设置环境变量 AMNOTE_VAULT。",
              file=sys.stderr)
        sys.exit(1)
    full = os.path.abspath(os.path.expanduser(raw))
    if os.path.exists(full) and not os.path.isdir(full):
        print(f"这不是文件夹：{full}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(full):
        print(f"找不到这个文件夹：{full}", file=sys.stderr)
        sys.exit(1)
    _vaults.clear()                               # 「唯一一本」：清掉再登记
    _bind(None)                                   # 钉着的那个对象也得松开，
                                                  # 不然 V() 还回刚清掉的那一本
    v = register(full, readonly=readonly, online=True, make_dirs=False)
    if not readonly:
        err = _ensure_dirs(v)
        if err:
            print(err, file=sys.stderr)
            sys.exit(1)
    return v.root


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

# 同步锁和「跑到哪儿了」按笔记本分家，挂在各自的 Vault 上（见上面 Vault）：
# 共用一把的话 A 本在扫 B 本就得排队，/__status 的进度也会互相串。


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

try:
    # config.json 的校验意见会随 /__config 回给页面，得跟着界面语言走。
    # 命令行跑 fulltext 时没人调 set_lang，语言就是默认的 zh-Hans，还是中文。
    from portal_i18n import T as _T, gloss as _gloss
except Exception:                                # 搬走了、或者它自己写坏了，索引都不能跟着倒
    def _T(key, **kw):
        return key.format(**kw) if kw else key

    def _gloss(key):
        return ""


def load_config(path=None):
    """读 config.json，逐项校验。返回 (配置, 问题列表)。"""
    if path is None:
        path = V().config_path
    cfg = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in DEFAULTS.items()}
    problems = []
    if not path or not os.path.exists(path):
        return cfg, problems
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        problems.append(str(_T("config.json 读不了，整份用默认值：{e}", e=e)))
        return cfg, problems
    if not isinstance(raw, dict):
        problems.append(str(_T("config.json 不是一个对象，整份用默认值")))
        return cfg, problems

    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        val = raw[key]
        if not isinstance(val, type(default)):
            problems.append(str(_T("config.json 的「{k}」类型不对，这一项用默认值",
                                   k=key, g=_gloss(key))))
            continue
        if not val and key in _MUST_FILL:
            problems.append(str(_T("config.json 的「{k}」是空的，这一项用默认值",
                                   k=key, g=_gloss(key))))
            continue
        cfg[key] = val

    pr = cfg["端口范围"]
    if not (len(pr) == 2 and all(isinstance(x, int) for x in pr)
            and 1 <= pr[0] <= pr[1] <= 65535):
        problems.append(str(_T("config.json 的「端口范围」不合法（{pr}），用默认值", pr=pr)))
        cfg["端口范围"] = list(DEFAULTS["端口范围"])
    return cfg, problems


def rules():
    """(跳过目录关键词, 噪声目录, 噪声文件)，跟着 config.json 的 mtime 走。

    老版本在 import 时把规则读死一次，改完设置得退出 app 才认。现在改完点一次
    重扫就生效——设置面板上那四项本来就是「改了要马上看效果」的东西。
    """
    v = V()
    cache = v.rules_cache                         # 缓存按本一份，不然规则会串库
    try:
        m = os.path.getmtime(v.config_path)
    except OSError:
        m = 0
    if cache["mtime"] != m:
        c, _ = load_config(v.config_path)
        cache["值"] = (tuple(c["跳过目录关键词"]),
                       tuple(c["噪声目录"]), tuple(c["噪声文件"]))
        cache["mtime"] = m
    return cache["值"]


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
    root = V().root
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d != ".amnote"
                       and not any(tok in d for tok in skip_dir)]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            is_doc = ext in (".md", ".html", ".htm")
            if not is_doc and ext not in ATT_EXT:
                continue
            if fn.startswith("~$"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if should_skip(rel):
                continue
            rel_u = rel.replace(os.sep, "/")
            if is_noise(rel_u):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            kind = ("md" if ext == ".md" else
                    "html" if ext in (".html", ".htm") else ATT_EXT[ext])
            out[rel_u] = (round(st.st_mtime, 2), st.st_size, kind)
    return out


def _full(rel):
    return os.path.join(V().root, rel.replace("/", os.sep))


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
    if not u.lower().endswith((".md", ".html", ".htm")):
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

def _connect_ro():
    """纯读地开索引库。**一个字节都不往库文件夹里写。**

    库是 WAL 模式的，而 WAL 的读者要一份 `-wal` 旁边的 `-shm`；`mode=ro` 不许
    建它，干净退出的库（AM·Note 关掉时会把 -wal / -shm 收走）于是连开都开不了。
    所以两步：

    1. 先按 `mode=ro` 开。`-shm` 还在（AM·Note 正开着、或者上次是崩的）时走这条，
       读的是带 WAL 的最新一致视图。
    2. 开不起来就退到 `immutable=1`——它把加锁和变动检测整个关掉，只读主库文件。
       **前提正是「没有 -shm」**：那说明这会儿没有别的连接开着，主库文件是完整的
       一份。之后就算 AM·Note 起来了，它的写先落 -wal，主库要到 checkpoint 才动，
       这一趟命令早读完了。

    退化的后果最多是「读到的是上一次 checkpoint 那一版」——命令行离线本来就是
    「用上次的索引」，stderr 上也这么说了。
    """
    uri = "file:" + urllib.parse.quote(V().db_path) + "?mode=ro"
    con = None
    try:
        con = sqlite3.connect(uri, timeout=30, uri=True)
        con.execute("PRAGMA query_only=1")
        # connect() 本身不真的去拿读锁，缺 -shm 要到第一条语句才炸。
        # 在这儿逼它现在就试，别把这个错漏给上面每一个调用点
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
        con = sqlite3.connect(uri + "&immutable=1", timeout=30, uri=True)
        con.execute("PRAGMA query_only=1")
    con.execute("PRAGMA mmap_size=536870912")
    return con


def _add_skeleton_col(con):
    """老库补「骨架」那一列，并把已经在库里的 md 一次性回填。

    **只能拿可写连接调。** 只读那条线（命令行离线：`mode=ro` / `immutable=1`）
    一个字节都不许往库里写，所以 connect() 的只读分支不经过这儿；那边读到的
    老库没有这一列，读侧自己容错（见 portal_server.tree_one）。

    回填从已经存着的 正文 现算，不碰磁盘、不动 mtime／大小——动了下一趟 sync
    会把整本库当成「全改过」，往流水里灌一屏假的「修改」。

    门户是多线程的，两条连接可能同时看到「没有这一列」。抢输的那条会吃一个
    `duplicate column name`，直接退出就是了：赢的那条接着回填，慢的那几秒里
    卡片上的纸是空的，下一次刷新就有了。

    （`skeleton_of` 和 `SK_SCAN` 在文件下半截，跟 `list_preview` 挨着。）
    """
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(文档)")}
    except sqlite3.Error:
        return
    if not cols or "骨架" in cols:
        return
    try:
        con.execute("ALTER TABLE 文档 ADD COLUMN 骨架 TEXT DEFAULT ''")
        con.commit()
    except sqlite3.OperationalError:
        return
    rels = [r for r, in con.execute("SELECT 路径 FROM 文档 WHERE 类型='md'")]
    for i in range(0, len(rels), 200):        # 一次 200 份：正文有 4 MB 一份的
        chunk = rels[i:i + 200]
        rows = con.execute(
            "SELECT 路径,substr(正文,1,%d) FROM 文档 WHERE 路径 IN (%s)"
            % (SK_SCAN, ",".join("?" * len(chunk))), chunk).fetchall()
        con.executemany("UPDATE 文档 SET 骨架=? WHERE 路径=?",
                        [(skeleton_of(b or ""), r) for r, b in rows])
        con.commit()


def connect():
    """开索引库。configure(readonly=True) 之后是**纯读**：URI 的 mode=ro，
    不改 journal_mode（那一句会写主库文件的头）、不建表。库不在就直接抛，
    调用方（命令行的离线通道）自己回一句「先打开 AM·Note」。"""
    if V().readonly:
        return _connect_ro()
    con = sqlite3.connect(V().db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA mmap_size=536870912")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS 文档(
      路径 TEXT PRIMARY KEY, 类型 TEXT, mtime REAL, 大小 INTEGER,
      正文 TEXT DEFAULT '', 原文 TEXT, 备注 TEXT DEFAULT '',
      骨架 TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS 链接(
      源 TEXT, 类型 TEXT, 目标 TEXT, 文本 TEXT);
    CREATE INDEX IF NOT EXISTS 链接_目标 ON 链接(目标);
    CREATE INDEX IF NOT EXISTS 链接_源 ON 链接(源);
    CREATE TABLE IF NOT EXISTS 元(键 TEXT PRIMARY KEY, 值 TEXT);
    """)
    _add_skeleton_col(con)
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
    bak_dir = V().backup_dir
    try:
        os.makedirs(bak_dir, exist_ok=True)
        flat = _flat(rel)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        name = f"{flat}__{stamp}.bak"
        with open(os.path.join(bak_dir, name), "w", encoding="utf-8") as f:
            f.write(old_text)
    except OSError:
        return ""
    # 同一份文件只留最近 BACKUP_KEEP 版（跟门户编辑那套同额同名，互相算在一起）
    try:
        olds = sorted(fn for fn in os.listdir(bak_dir)
                      if fn.startswith(flat + "__") and fn.endswith(".bak"))
        for fn in olds[:-BACKUP_KEEP]:
            os.remove(os.path.join(bak_dir, fn))
    except OSError:
        pass
    _prune_backups()
    return name


def _prune_backups():
    """备份目录总大小超上限就从最旧的删起。Agent 批量改几百份时别让它无限长。"""
    bak_dir = V().backup_dir
    try:
        fns = [(fn, os.path.getmtime(os.path.join(bak_dir, fn)),
                os.path.getsize(os.path.join(bak_dir, fn)))
               for fn in os.listdir(bak_dir) if fn.endswith(".bak")]
    except OSError:
        return
    total = sum(s for _, _, s in fns)
    cap = BACKUP_TOTAL_MB * 1024 * 1024
    if total <= cap:
        return
    for fn, _, s in sorted(fns, key=lambda x: x[1]):
        try:
            os.remove(os.path.join(bak_dir, fn))
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
    p = os.path.join(V().backup_dir, name)
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

#
# **5.6：每一笔另记「是谁写的」。** 带了 X-AMN-Agent 头的 POST 是本机某个 AI
# 助手（Claude Code、Codex…）在写，流水上要看得出来，不能跟用户自己在门户里
# 敲的那些混成一档。所以表里存的是 (时刻, 代理名字)，认领时把名字一起交回去：
# 认领的结果是一对 `(认领到没有, 署名)`，**不是一个字符串**：署名是用户给的，
# `--agent 门户` 那样叫一声就不该顶掉「这是用户自己写的」这条判断（原来把
# 「没认领到」「门户」「Agent 名字」挤在同一个字符串上，两个哨兵值都能被冒名）。

_pw_lock = threading.Lock()
# 键是 **(vid, 相对路径)**：只按相对路径记的话，两本里同名的一份笔记会互相
# 认领——A 本门户保存的那一笔被 B 本那趟同步消费掉，A 本的流水就落成「外部」。
_portal_writes = {}                   # {(vid, 相对路径): [(写入时间, 代理名字), ...]}
PW_WINDOW = 120                       # 保存时间和文件 mtime 差这么多秒内算同一笔
PW_FILES = 500                        # 活表最多盯这么多份文件

SRC_PORTAL = "门户"                    # 认领到、但没署名 → 用户自己在门户里写的
SRC_AGENT = "Agent"                   # 认领到、而且署了名 → 流水的「来源」写这个
SRC_EXTERNAL = "外部"                  # 没认领到 → 别的编辑器 / 脚本动的


def _pw_prune(now):
    """掐掉过了窗口的旧笔，顺带清空的键。调用方必须已经拿着锁。"""
    for k in list(_portal_writes):
        keep = [w for w in _portal_writes[k] if now - w[0] < PW_WINDOW]
        if keep:
            _portal_writes[k] = keep
        else:
            _portal_writes.pop(k, None)


def note_portal_write(rel, agent=None):
    """门户里每写成功一笔（/__save）就记一条。portal_server 落盘前就叫。

    `agent`＝X-AMN-Agent 头里那个名字（没有就是用户自己在门户里写）。

    **一份文件挂一串时间，不是一个格子。** 停笔 2 秒落一次盘，一趟同步跑着的时候
    可能已经又存了两回；同步这边是拿两份快照比 (mtime, 大小)，同一份文件因此
    可能被前后两趟同步各看见一次改动。一个格子只够认领一次，第二次就落空、
    判成「外部」，白留一版档还在流水上多记一行——这正是 20260829 沙箱里
    「3 次门户保存出来 1 条门户 ＋ 1 条外部」的成因。
    """
    if not rel:
        return
    now = time.time()
    key = (V().vid, rel)
    with _pw_lock:
        _portal_writes.setdefault(key, []).append((now, (agent or "").strip()))
        _pw_prune(now)
        if len(_portal_writes) > PW_FILES:        # 兜底，正常到不了
            for k in list(_portal_writes)[:len(_portal_writes) - PW_FILES]:
                _portal_writes.pop(k, None)


def take_portal_write(rel, mtime, window=PW_WINDOW):
    """这份文件这一次改动是不是门户自己写的。认领哪一笔就消费哪一笔。

    按「离这次 mtime 最近」挑，不是先进先出：同步看见的先后跟保存的先后
    不保证一致，挑最近的那笔才对得上号。

    返回 `(认领到没有, 署名)`：`(False, "")`＝外部；`(True, "")`＝门户自己写的；
    `(True, "名字")`＝那个 Agent 写的。署名跟「认领到没有」分成两个值，
    署名叫「门户」也冒充不了用户自己那一档。
    """
    now = time.time()
    key = (V().vid, rel)
    with _pw_lock:
        _pw_prune(now)
        lst = _portal_writes.get(key)
        if not lst:
            return (False, "")
        i = min(range(len(lst)), key=lambda j: abs(mtime - lst[j][0]))
        if abs(mtime - lst[i][0]) >= window:
            return (False, "")
        who = lst.pop(i)[1]
        if not lst:
            _portal_writes.pop(key, None)
        return (True, who)


# ── 门户搬动活表：跟上面那张表同一个套路，只是记「搬位置的」 ───────
#
# /__trash 把一份文件挪进系统废纸篓，/__untrash 再把它挪回来。文件在库里
# 消失和出现，下一趟同步照样会在 removed / added 里看见——不认领的话就记成
# 「删除／外部」「新增／外部」，用户在门户里点的那两下会在流水上留下
# 「不是我干的」。所以两条路由动文件之前都先在这里记一笔，_sync_once 认领掉，
# 来源记「门户」。
#
# **只管来源那一列，留档照留。** 外部删除会把 db 里的原文存进 backups/，
# 门户删除同样需要那一份：文件躺在废纸篓里，用户清空废纸篓之后，
# backups/ 里那一版就是最后的退路。
#
# **搬位置的不能用上面那张写入表。** 那张表按「记账时刻离文件 mtime 多近」
# 认领，而搬文件不动 mtime：一份上周写的笔记今天删了再撤销，mtime 还是上周，
# 跟记账时刻差着几天，一条都认不上（而且 _pw_prune 按记账时刻剪枝，把 mtime
# 直接塞进去也活不过一轮）。所以这张表只看「窗口之内记过没有」，不比 mtime。
#
# 一份文件只记一个时刻、不排队：删和撤销必然交替出现，同一个方向连着来两次
# 中间一定隔着另一次同步。

_pm_lock = threading.Lock()
_portal_moves = {}                    # {(vid, 相对路径): (记账时刻, 代理名字)}
PM_WINDOW = 300                       # 记了这么多秒还没被同步认领就作废


def note_portal_move(rel, agent=None):
    """门户里每搬一份（/__trash 或 /__untrash）就记一条。动文件之前叫，
    理由同 note_portal_write：晚一步就可能被正在跑的那趟同步判成「外部」。

    `agent` 同 note_portal_write：署了名的那些流水上记「Agent」。"""
    if not rel:
        return
    now = time.time()
    key = (V().vid, rel)
    with _pm_lock:
        _portal_moves[key] = (now, (agent or "").strip())
        for k in list(_portal_moves):
            if now - _portal_moves[k][0] >= PM_WINDOW:
                _portal_moves.pop(k, None)


def take_portal_move(rel, window=PM_WINDOW):
    """这份文件这一次进出库根是不是门户自己搬的。查中即消费，一笔只认领一次。

    返回值同 take_portal_write：`(认领到没有, 署名)`。
    搬失败的那条路上也叫它一次，把刚记的那笔收回来。
    """
    now = time.time()
    key = (V().vid, rel)
    with _pm_lock:
        rec = _portal_moves.pop(key, None)
    if rec is None or now - rec[0] >= window:
        return (False, "")
    return (True, rec[1])


# ── 变更流水 ────────────────────────────────────────────────

JOURNAL_MERGE_GAP = 600               # 门户连续保存的合并窗口（10 分钟）


def _journal_append(entries):
    if not entries:
        return
    try:
        with open(V().journal, "a", encoding="utf-8") as f:
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
    journal = V().journal
    try:
        with open(journal, encoding="utf-8") as f:
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

    tmp = journal + ".tmp"                            # 先写临时文件再改名
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for r in recs:
                f.write((json.dumps(r, ensure_ascii=False)
                         if isinstance(r, dict) else r) + "\n")
            for e in add:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, journal)
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
        with open(V().journal, encoding="utf-8") as f:
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
    v = V()
    if not v.sync_lock.acquire(blocking=False):
        v.state["待重跑"] = True
        return {"ok": False, "说明": "已有一趟在跑，跑完会自动补一轮"}
    v.state["运行中"] = True
    try:
        r = _sync_once(log or (lambda *a: None))
    finally:
        v.state["运行中"] = False
        v.sync_lock.release()
    if v.state["待重跑"]:
        v.state["待重跑"] = False
        return sync(log)
    return r


def _sync_once(log):
    # 库根还在吗。外置盘拔了 ／ 文件夹被挪走 ／ 正在被「重新定位」的那几秒里，
    # walk_files() 回一个空表，下面就会把整本索引删光、往流水里灌一屏「删除」、
    # 顺手给每一份都留一次档——而磁盘上那些文件一份都没少。
    # portal 的 recheck_offline 每 3 秒探一次，回来了自动补一轮，这里直接不干活。
    v = V()
    if not os.path.isdir(v.root):
        log("库根现在不在，这一趟跳过：%s" % v.root)
        return {"ok": False, "说明": "库根现在不在，这一趟跳过", "库根": v.root}
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
    # 搬位置的（移到废纸篓 / 撤销）走另一张表：它们不动 mtime，比不了时间。
    # 认领到就别再记成「外部」——用户在门户里点的那一下不是外人干的。
    # 留档照留，见 note_portal_move 上面那段
    # 两张表都得问一遍：写成 `A or B` 的话，A 认领到就把 B 短路了，搬动表里那一笔
    # 留在原地，300 秒之内下一次真的外部改动就被它顶着记成「门户」。
    # 5.6：认领到的那一笔另带一个署名。署了名的来源记「Agent」＋一个 代理 字段，
    # 门户和外部两个既有值一个字不动。
    def _who(claim):
        got, agent = claim
        if not got:
            return (SRC_EXTERNAL, "")
        if not agent:
            return (SRC_PORTAL, "")
        return (SRC_AGENT, agent)

    def _claim(r):
        w = take_portal_write(r, cur[r][0])
        m = take_portal_move(r)
        return _who(w if w[0] else m)

    src = {r: _claim(r) for r in added + changed}
    for r in removed:
        src[r] = _who(take_portal_move(r))

    def src_of(rel):
        return src.get(rel, (SRC_EXTERNAL, ""))[0]

    def agent_of(rel):
        return src.get(rel, (SRC_EXTERNAL, ""))[1]

    def _ev(e, rel):
        """事件加上署名（没署名就不加这个键，别在流水里堆一列空串）。"""
        ag = agent_of(rel)
        if ag:
            e["代理"] = ag
        return e

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
            events.append(_ev({"时间": now_iso, "事件": "新增", "路径": r,
                               "类型": cur[r][2], "来源": src_of(r),
                               "大小KB": round(cur[r][1] / 1024, 1)}, r))
        for r in sorted(changed):
            events.append(_ev({"时间": now_iso, "事件": "修改", "路径": r,
                               "类型": cur[r][2], "来源": src_of(r),
                               "大小KB": round(cur[r][1] / 1024, 1),
                               "留档": baks.get(r, "")}, r))
        for r in sorted(removed):
            events.append(_ev({"时间": now_iso, "事件": "删除", "路径": r,
                               "类型": old[r][2], "来源": src_of(r),
                               "留档": baks.get(r, "")}, r))
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
        # 骨架只有 md 有（html 的卡片画的是一扇窗，pdf／表格不在门户里列）
        con.execute("INSERT OR REPLACE INTO 文档(路径,类型,mtime,大小,正文,原文,备注,骨架) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (r, kind, cur[r][0], cur[r][1], body, raw, note,
                     skeleton_of(body) if kind == "md" else ""))
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
            con.execute("INSERT OR REPLACE INTO 文档(路径,类型,mtime,大小,正文,原文,备注,骨架) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (r, "pdf", cur[r][0], cur[r][1], body, None, note, ""))
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
    db_path = V().db_path
    before = os.path.getsize(db_path)
    con.execute("VACUUM")
    con.close()
    r = {"ok": True, "重抽": len(rows), "有正文": n_ok, "空的": n_bad,
         "去重复副本": dup,
         "库MB": {"前": round(before / 1048576, 1),
                  "后": round(os.path.getsize(db_path) / 1048576, 1)},
         "耗时秒": round(time.time() - t0, 1)}
    log(f"索引瘦身：{r}")
    return r


# ── 搜索 ────────────────────────────────────────────────────

def _like_esc(t):
    return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ── md 标题（大纲、片段的「小节」、库地图共用一份）───────────
#
# 一份 md 的 ATX 标题。三处都要它，口径必须是同一套，不然 /__outline 说这份有
# 五个小节、/__raw?section= 却找不到其中一个，Agent 就没法照着大纲取正文了。

_ATX_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_ATX_TAIL_RE = re.compile(r"\s+#+\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def _atx_text(s):
    """ATX 标题的文本：只在收尾的 `#` 前面有空白时才当作闭合记号剥掉。

    `## 打包 ##` → `打包`；`## C#` → `C#`（CommonMark 的口径）。原来一律
    rstrip("#")，`C#`、`F#`、`目标 #1` 这些标题在大纲和 `--section` 里
    对不上号。
    """
    return _ATX_TAIL_RE.sub("", s.strip()).strip()


def md_headings(text):
    """md 里的 ATX 标题，返回 [{级, 文本, 行, 偏移}]。

    跳两样东西，跟 first_heading 一个理由：开头的 frontmatter（里面的
    `标题: xxx` 不是 markdown 标题），以及 fenced code——库里的施工类
    md 常带 bash 片段，`# 装依赖` 那种注释行不是小节。

    围栏按 CommonMark 认：开的那一道记住它有几个记号，**只有同一种记号、
    不比它短的一道才关得上**。`````` ```` `````` 里面套一道 ```` ``` ````
    是合法写法（外层要包住内层），照 3 个字符比的话第一道内层就把外层关了，
    后面整份文件的标题跟着错位。

    frontmatter 那一道在窗口内找不到收尾就当没有 frontmatter（start = 0）：
    一份以 `---` 开头、没写收尾的 md，正文里的标题照样是标题。

    行是 1-based；偏移是这一行行首在全文里的字符下标，搜索片段按它回找
    「这一处命中落在哪一节里」。
    """
    out = []
    if not text:
        return out
    lines = text.split("\n")
    start = 0
    if lines[0].strip() in ("---", "+++"):            # 开头的 frontmatter
        mark = lines[0].strip()
        for j in range(1, min(len(lines), 200)):
            if lines[j].strip() == mark:
                start = j + 1
                break
    fence = ""                                        # 开着的那一道围栏的原样记号
    off = 0
    for i, ln in enumerate(lines):
        if i >= start:
            s = ln.lstrip()
            m = _FENCE_RE.match(s)
            if m:
                tok = m.group(1)
                if not fence:
                    fence = tok
                elif tok[0] == fence[0] and len(tok) >= len(fence):
                    fence = ""
            elif not fence:
                m = _ATX_RE.match(ln)
                if m:
                    out.append({"级": len(m.group(1)),
                                "文本": _atx_text(m.group(2)),
                                "行": i + 1, "偏移": off})
        off += len(ln) + 1
    return out


def _head_at(heads, pos):
    """`pos` 这个字符位置落在哪一个标题底下。没有就返回 None。"""
    hit = None
    for h in heads:
        if h["偏移"] <= pos:
            hit = h
        else:
            break
    return hit


def snippets(body, terms, per_term=2, radius=42, heads=None):
    """正文里每个词取前几处命中，带前后文。返回 [{前,中,后,行,小节?}]。

    `行` 是 1-based，按命中位置前面有几个换行算。`heads` 给了（md 才给，
    见 md_headings）就再带一个 `小节`＝命中位置之前最近的那个标题；这一份没有
    标题时省略这个键——写成空串的话，调用方分不清「不在任何小节里」和
    「小节名是空的」。
    """
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
            one = {
                "前": ("…" if a > 0 else "") + body[a:i].replace("\n", " "),
                "中": body[i:i + len(tl)],
                "后": body[i + len(tl):b].replace("\n", " ") + ("…" if b < len(body) else ""),
                "行": body.count("\n", 0, i) + 1,
            }
            h = _head_at(heads, i) if heads else None
            if h and h["文本"]:
                one["小节"] = h["文本"]
            out.append(one)
            pos = i + len(tl)
            n += 1
    return out[:4]


# ── 标题、切片、库地图：门户和命令行共用的那一份 ─────────────
#
# 5.6 之前这几样各写了两遍（portal_server 一份、amnote_cli 一份）。地图的排版、
# `--lines A-B` 的钳位、「一句话」的口径只要有一处改了、另一处忘了跟，
# `amnote map` 在 AM·Note 开着和没开着的时候就会印出两份不一样的地图，
# 而 Agent 正是靠这份地图导航的。所以**只留这一份**，两边都从这里取。

TAG_HEAD_RE = re.compile(r"^\s*---.*?\n---\s*", re.S)


def first_heading(text):
    """md 正文里第一个 `# ` 标题。

    要跳两样东西：开头的标签块（里面的 `标题: xxx` 不是 markdown 标题），
    以及代码块——库里的施工类 md 常带 bash 片段，`# 装依赖` 那种注释行
    会被整份当成标题。只看前 400 行，再往后才出现的一级标题不算文档标题。
    """
    if not text:
        return ""
    fence = False
    for ln in TAG_HEAD_RE.sub("", text, count=1).splitlines()[:400]:
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            continue
        if fence or not s.startswith("# "):
            continue
        return _atx_text(s[2:])
    return ""


def clean_title(fn):
    """文件名主干：剥掉 _vN 和八位日期，下划线换空格。库里的命名规矩是
    `名称_vN_YYYYMMDD.md`，那两截在列表里另有一格，标题里再写一遍是重复。"""
    t = os.path.splitext(fn)[0]
    t = re.sub(r"_v[\d.]+(?=_|$)", "", t, flags=re.I)
    t = re.sub(r"_?20\d{6}(?=_|$)", "", t)
    return t.replace("_", " ").strip(" ·-—") or os.path.splitext(fn)[0]


def html_title(head):
    """html 抽取正文把 <title> 放在第一行。太长就当正文，不当标题。"""
    if not head:
        return ""
    line = (head or "").split("\n", 1)[0].strip()
    if not line or len(line) > 80:
        return ""
    return line


# ── 副行要的是「第一段散文」，所以洗的时候先整行扔掉不是散文的那些 ────
#
# 5.10 首页列表的副行取的是这份摘录的第一句（brief §1.3-1）。老洗法只剥
# frontmatter、井号和图片，一份「整页就是一张表」的笔记洗完剩一串
# `问题 条数 占比 触点氧化 412` 的碎词，当副行读起来像乱码。
# 补的几条都是**丢行**，不改剩下那些行里的任何一个字：
#   · 标题行 / 围栏代码块 / 引用行（_pv_body，整块丢；brief §1.3-1 点名的三条，
#     5.10 审查 S1 补上的。它必须赶在剥记号之前，见那个函数的注释）
#   · 表格行（`|` 开头，跟骨架的 SK_TABLE 同一条）
#   · 只有一条链接的行（markdown 链接或裸 URL，前面允许一个列表记号）
#   · 纯日期行（`2026-08-31` / `2026年8月31日` / 后面跟个时刻也算）
#   · 列表项（整条清单交给下面那一档，不按长短拆开）
#   · 少于 PV_MIN 个字的行——「完」「见下」这类碎句；
#     它们进了副行只会占位，不给「点开的理由」。
# 一行合格散文都没有的时候还有一档兜底：把列表项串成一句
# （`- 牛奶 / - 面包 / - 苹果` → `牛奶、面包、苹果`）。购物清单、检查表这类笔记
# 本来就没有散文，但那串项目名恰恰是它最有用的一行摘要；`- [ ]`/`- [x]`
# 的勾选框一并剥掉——勾没勾是文档里的事，副行只要那句话。
# 两档都空（整页只有一张表 / 一张图）才真的回空，列表那边再回落文件夹路径。
PV_TABLE_RE = re.compile(r"^ {0,3}\|")
PV_LINK_RE = re.compile(
    r"^ {0,3}(?:[-*+]\s+)?(?:\[[^\]]*\]\([^)]*\)|<?https?://\S+>?)[ \t]*$")
PV_DATE_RE = re.compile(
    r"^ {0,3}(?:\d{4}\s*[-/.年]\s*)?\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?"
    r"(?:[\s,，]*\d{1,2}:\d{2}(?::\d{2})?)?[ \t.。、·\-–—]*$")
PV_MIN = 8
PV_CAP = 240            # 存进索引的上限。页面副行再从这里截 60 字
PV_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(.*)$")
PV_TASK_RE = re.compile(r"^\[[ xX]\][ \t]*")
# brief §1.3-1 的另外三条「跳过」：标题 / 代码 / 引用。这三条是**整块丢**，
# 所以不在 _pv_keep 里（它一次只看一行，看不见围栏的开合），走 _pv_body。
# 口径跟骨架的 SK_FENCE / SK_H / SK_QUOTE 一样（那组常量在这个文件往下 30 行，
# 这一段要能自己读得懂才另写一份）；哪天改了记得两边一起看。
PV_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
PV_HEAD_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
PV_QUOTE_RE = re.compile(r"^ {0,3}>")


def _pv_body(text):
    """丢掉标题行、围栏代码块（连围栏一起）、引用行，剩下的原样返回。

    **必须在剥 `#` 之前做。** 先把记号剥了，标题就跟散文长得一模一样，只要不短于
    PV_MIN 就照收——5.10 审查 S1 实测出来的三种副行：从 H2 标题开头、一整行
    `docker compose up -d …`、连 `>` 都留着的引文。代码和引用则原本一条规则都没有。
    没收尾的围栏一路吃到文末（跟 skeleton_of 同一个处置），洗出来是空的，
    列表那边回落文件夹路径。
    """
    out = []
    fence = ""
    for line in text.split("\n"):
        m = PV_FENCE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence:
                fence = ""
            continue
        if m:
            fence = m.group(1)[0]
            continue
        if PV_HEAD_RE.match(line) or PV_QUOTE_RE.match(line):
            continue
        out.append(line)
    return out


def _pv_items(text):
    """把正文里的列表项用「、」串成一句：剥掉 - * + / 1. 记号和 [ ] [x] 勾选框。
    只在一行合格散文都没有时才走这一档，所以**不挑长短**——一条清单要么整条进来，
    要么一条都不进；按长度筛会筛出「写发版说明 跑 i18n_check」这种缺了中间一项的洞。"""
    out = []
    for line in text.split("\n"):
        m = PV_ITEM_RE.match(line.strip())
        if not m:
            continue
        t = PV_TASK_RE.sub("", m.group(1).strip(), count=1).strip()
        if t:
            out.append(t)
    return "、".join(out)


def _pv_keep(line):
    """这一行算不算「散文」：不是表格 / 链接 / 纯日期 / 列表项，且不少于 PV_MIN 个字。
    （标题 / 代码 / 引用在 _pv_body 里已经整行丢掉了。）

    **长度那一刀放在四条正则之前**：这串要对每篇笔记逐行跑一遍，而它挂在一条
    每小时至少重算一次、索引一动就重跑的路上（勾一条待办也补扫一次，审查 S7）。
    中文一个字一个码位，短行占大多数，len(s) 不花钱就能挡掉一大半。
    先按整行长度筛跟原来的判据是一回事：非空白字符数不可能多过整行长度。"""
    s = line.strip()
    if len(s) < PV_MIN:
        return False
    if (PV_TABLE_RE.match(s) or PV_LINK_RE.match(s)
            or PV_DATE_RE.match(s) or PV_ITEM_RE.match(s)):
        return False
    return len(re.sub(r"\s", "", s)) >= PV_MIN


def list_preview(head, kind):
    """列表和本地搜索用的短摘录。html 已经是抽过的纯文本。"""
    s = head or ""
    if kind == "md":
        s = TAG_HEAD_RE.sub("", s, count=1)
        s = "\n".join(_pv_body(s))     # 标题 / 代码 / 引用整块丢，得赶在剥记号之前
        s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    # 凑够 PV_CAP 就收手：结果只要前 240 字，后面那几千字一行都不用看
    # （逐行收、收满即停，跟「整篇洗完再切」出来的串一模一样：合并空白只会变短，
    #   按合并后的长度记账就不会提前切）。审查 S7 说的那条每小时重跑的路上，
    #   这一刀比省几条正则管用。
    parts, got = [], 0
    for ln in s.split("\n"):
        if not _pv_keep(ln):
            continue
        t = re.sub(r"\s+", " ", ln).strip()
        parts.append(t)
        got += len(t) + 1
        if got > PV_CAP:
            break
    if parts:
        return " ".join(parts)[:PV_CAP]
    # 一行散文都没有：退到列表项那一档（页面再截到 60 字当副行）
    return re.sub(r"\s+", " ", _pv_items(s)).strip()[:PV_CAP]


# ── 骨架：一份 md 的结构缩影，索引时算一次 ───────────────────
#
# 输出是一串字母，一个字母一个块：h 标题 · p 段落 · l 列表 · t 表格 · c 代码 ·
# i 图片 · q 引用，最多 SK_MAX 个。首页的卡片照这串字画一张「纸」，所以同一份
# md 必须永远算出同一串——规则里没有一处随机，也不看文件名、时间、长度。
#
# 规则与页面上的 skeletonOf() **逐条相同**（5.9 需求轮 B-report §2.1，源码在
# _工作区/20260911_需求迭代_v1/cards/parts/app.js）。两边哪天改，得一起改：
# 骨架是存在库里的，页面只是把它画出来，对不上就是卡片上的纸跟文档长得不一样。
#
# 判断顺序是硬要求：围栏 → 分隔线 → 标题 → 图片 → 引用 → 表格 → 列表 → 段落。
# 围栏必须最先（代码块里可能有 `#` `|` `-`），分隔线必须排在标题之前（`---`
# 否则会被别的规则误伤）。第一个一级标题吞掉不计——那是文档标题，卡片的纸上
# 已经单写了一行，再画一根横条就是写了两遍。
SK_MAX = 14
SK_SCAN = 8000          # 只看开头这些字。14 个块之前早到了，别拿 4 MB 跑正则
SK_FM = re.compile(r"^\ufeff?\s*---[ \t]*\n[\s\S]*?\n---[ \t]*(?:\n|$)")
SK_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
SK_HR = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
SK_H = re.compile(r"^ {0,3}(#{1,6})[ \t]+\S")
SK_IMG = re.compile(r"^ {0,3}!\[[^\]]*\]\([^)]*\)[ \t]*$")
SK_QUOTE = re.compile(r"^ {0,3}>")
SK_TABLE = re.compile(r"^ {0,3}\|")
SK_LIST = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")
SK_CONT = re.compile(r"^[ \t]+\S")     # 列表项的缩进续行，跟着上一条一起吃
SK_CLOSE = {"`": re.compile(r"^ {0,3}`{3,}"),
            "~": re.compile(r"^ {0,3}~{3,}")}


def skeleton_of(text):
    """md 原文 → 骨架串。**只对 md 调**，别的类型一律存空串。"""
    # 超长的只看开头一截。**末尾那半行照留**：切在行首反而会把整段丢掉
    # （开头就是一整段 9000 字的文档，切到行边界就只剩标题、骨架成了空串）。
    # 半行最多让最后一个块判错一次类型，比少一个块轻。
    s = re.sub(r"\r\n?", "\n", str(text or "")[:SK_SCAN])
    s = SK_FM.sub("", s, count=1)      # 只剥开头那一块 frontmatter
    lines = s.split("\n")
    n = len(lines)
    out = []
    i = 0
    h1done = False
    while i < n and len(out) < SK_MAX:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        f = SK_FENCE.match(ln)         # 围栏代码块：一路吃到收尾围栏
        if f:
            close = SK_CLOSE[f.group(1)[0]]
            i += 1
            while i < n and not close.match(lines[i]):
                i += 1
            i += 1
            out.append("c")
            continue
        if SK_HR.match(ln):            # 分隔线：不是块
            i += 1
            continue
        h = SK_H.match(ln)             # 标题：第一个一级标题吞掉不计
        if h:
            i += 1
            if len(h.group(1)) == 1 and not h1done:
                h1done = True
                continue
            out.append("h")
            continue
        if SK_IMG.match(ln):           # 单独一行的图片
            i += 1
            out.append("i")
            continue
        if SK_QUOTE.match(ln):         # 引用 / 表格 / 列表：连着的几行算一个块
            while i < n and SK_QUOTE.match(lines[i]):
                i += 1
            out.append("q")
            continue
        if SK_TABLE.match(ln):
            while i < n and SK_TABLE.match(lines[i]):
                i += 1
            out.append("t")
            continue
        if SK_LIST.match(ln):
            while i < n and (SK_LIST.match(lines[i]) or SK_CONT.match(lines[i])):
                i += 1
            out.append("l")
            continue
        while i < n:                   # 剩下的都是段落：吃到空行或下一个块起头
            x = lines[i]
            if not x.strip():
                break
            if (SK_FENCE.match(x) or SK_HR.match(x) or SK_H.match(x)
                    or SK_QUOTE.match(x) or SK_TABLE.match(x)
                    or SK_LIST.match(x) or SK_IMG.match(x)):
                break
            i += 1
        out.append("p")
    return "".join(out)


def lines_of(text):
    """按 \\n 切行，末尾那个空串不算一行。

    行号在三处要对得上：md_headings 的「行」、/__raw 的 lines=A-B、/__outline
    的「行数」。所以只在这一处定义「第几行」是什么意思。
    """
    ls = (text or "").split("\n")
    if ls and ls[-1] == "":
        ls.pop()
    return ls


LINES_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def md_section(text, want):
    """标题文本 → (行起, 行止)，1-based 闭区间。找不到返回 None。

    先精确匹配，再不分大小写前缀匹配，都取第一个。范围是这一行起、到下一个
    **同级或更高级**标题的前一行为止——`## 打包` 底下的 `### 细节` 是它的一部分，
    要一起给出来。
    """
    want = (want or "").strip()
    if not want:
        return None
    heads = md_headings(text)
    hit = None
    for h in heads:
        if h["文本"] == want:
            hit = h
            break
    if hit is None:
        low = want.lower()
        for h in heads:
            if h["文本"].lower().startswith(low):
                hit = h
                break
    if hit is None:
        return None
    end = len(lines_of(text))
    for h in heads:
        if h["行"] > hit["行"] and h["级"] <= hit["级"]:
            end = h["行"] - 1
            break
    return hit["行"], max(hit["行"], end)


def text_slice(text, section="", lines=""):
    """按 `lines=A-B` 或 `section=<标题>` 切一段。两个都给时 lines 说了算。

    返回 ({正文, 行起, 行止, 行数}, 错误代号)。错误代号是 ASCII 短码，
    调用方自己翻成话：`bad_lines`（A-B 写错了）、`out_of_range`（起点在
    全文之后）、`no_section`（这份里没有这一节）。没出错就是空串。

    A > B 一律**对调**，不各钳各的：`--lines 5-2` 明显是手打反了，
    给第 2–5 行比给一行第 5 行更像用户要的。
    """
    ls = lines_of(text)
    total = len(ls)
    a, b = (1 if total else 0), total
    body = text
    spec = (lines or "").strip()
    if spec:
        m = LINES_RE.match(spec)
        if not m:
            return None, "bad_lines"
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            a, b = b, a
        a = max(1, a)
        if a > total:
            return None, "out_of_range"
        b = min(total, b)
        body = "\n".join(ls[a - 1:b])
    elif (section or "").strip():
        span = md_section(text, section)
        if span is None:
            return None, "no_section"
        a, b = span
        body = "\n".join(ls[a - 1:b])
    return {"正文": body, "行起": a, "行止": b, "行数": total}, ""


# ── 库地图 ────────────────────────────────────────────────────
#
# 一份 Markdown 的「这个库里有什么」，给 Agent 当导航图：先看地图，再决定搜哪个
# 词、读哪一篇的哪一节，不用把几百份笔记的正文全灌进上下文。
#
# **只吃索引里的行，不读磁盘。** 调用方把 (路径, 类型, mtime, 大小, 正文前 4000 字)
# 交进来就行——门户从 fulltext.db 查，命令行离线时也从同一张表查。

MAP_SKIP = ("库地图.md", "AGENTS.md")   # 地图自己和给 Agent 的说明书不进地图
MAP_MAX = 20                            # 每个目录默认列几篇
MAP_MAX_CAP = 200
MAP_STEPS = (20, 10, 5, 3, 2)           # 超预算时依次减半重画
MAP_DEPTH_CAP = 12
MAP_BUDGET = 40_000                     # 字符预算。上下文是有价的，地图不能没边
MAP_BUDGET_CAP = 200_000
MAP_RECENT = 10
MAP_ONE = 80                            # 「一句话」的字数上限
MAP_GIST_MIN = 24                       # 「一句话」不到这么长就再往下接一段
MAP_FILE = "库地图.md"
MAP_NOTE = "<!-- 由 AM·Note 生成，可随时重新导出覆盖 -->"

_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_WIKI = re.compile(r"\[\[([^\[\]|\n]+)\]\]")
_MD_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_CJK_TAIL = re.compile(r"[　-〿㐀-鿿＀-￯]$")


def _demark(s):
    """一行 markdown → 一行人话。链接只留文字，图片、强调记号、行首标记去掉。
    下划线**不动**——库里的文件名满是 `_v2_20260901` 这种，去了反而认不出。"""
    s = _MD_IMG.sub(" ", s or "")
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_WIKI.sub(r"\1", s)
    s = re.sub(r"^\s*(?:[>\-+*]\s+|\d+[.)]\s+)+", "", s)
    s = re.sub(r"[*`~]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _stamp(ts, fmt):
    try:
        return datetime.fromtimestamp(ts or 0).strftime(fmt)
    except (ValueError, OSError, OverflowError):
        return ""


def _size_text(n):
    n = n or 0
    if n >= 1048576:
        return "%.1fMB" % (n / 1048576.0)
    return "%.1fKB" % (n / 1024.0)


def _join_gist(a, b, listy=False):
    """接上一行。

    · 上一行来自清单（`- 牛奶`）就用顿号接：一份购物清单的三行拼成
      `牛奶 面包 苹果` 看着像一句断了的话，`牛奶、面包、苹果` 才是它。
    · 其余按语种：中日韩的行之间不加空格（源文里换行只是排版），
      拉丁文之间要加——不然 `the` ＋ `quick` 会粘成 `thequick`。
    """
    if not a:
        return b
    if listy:
        return a + "、" + b
    if _CJK_TAIL.search(a) or _CJK_TAIL.match(b[:1]):
        return a + b
    return a + " " + b


def _map_gist(head, kind, title):
    """地图上那句「一句话」：正文第一段，去 markdown，≤ MAP_ONE 字。

    「一段」是连着的几行——源文里的换行多半只是排版，`周六：整理书桌。` 和
    `周日：去公园散步。` 是同一段话的两行，拆开看都认不出这是哪一篇。
    空行才算段落到头；但**第一段太短（不到 MAP_GIST_MIN 字）就再接一段**：
    「这是一份示例笔记。」自己站着等于没说。标题、fenced code、表格分隔行
    都不算正文。
    """
    if kind not in ("md", "html"):
        return ""
    s = TAG_HEAD_RE.sub("", head or "", count=1) if kind == "md" else (head or "")
    fence = False
    got = ""
    for ln in s.splitlines():
        t = ln.strip()
        if t.startswith("```") or t.startswith("~~~"):
            fence = not fence
            continue
        if not t:
            if len(got) >= MAP_GIST_MIN:         # 空行＝一段到头，够长就收工
                break
            continue
        if fence:
            continue
        if kind == "md" and t.startswith("#"):
            if len(got) >= MAP_GIST_MIN:         # 下一个小节的标题，别再往下接
                break
            continue
        if set(t) <= set("|-:= "):               # 表格分隔行、setext 的下划线
            continue
        listy = bool(_MD_LIST.match(t))
        t = _demark(t)
        if not t or t == (title or "").strip():  # html 抽出来的第一行就是标题
            continue
        got = _join_gist(got, t, listy)
        if len(got) >= MAP_ONE:
            break
    return got[:MAP_ONE] + ("…" if len(got) > MAP_ONE else "")


def _map_line(d, key):
    """一篇一行：`文件名 · 标题 · 一句话 · 改于日期 · 大小`。

    没有 `# 标题` 的那些省掉标题段——那时候标题只能退回文件名，行首已经写过了。
    附件用 `[pdf]` 代替标题和一句话：它们的正文是抽出来的文本层，没有「第一段」。
    `key` 是所在的目录，文件名按它相对写（depth 折叠时会是 `二级/三级/某篇.md`）。
    """
    p = d["路径"]
    name = p[len(key) + 1:] if key and p.startswith(key + "/") else p
    bits = [name]
    if d["类型"] in ("md", "html"):
        if d["标题"]:
            bits.append(d["标题"])
        if d["一句话"]:
            bits.append(d["一句话"])
    else:
        bits.append("[%s]" % (d["类型"] or "附件"))
    bits.append(_stamp(d["时"], "%Y-%m-%d"))
    bits.append(_size_text(d["大小"]))
    return " · ".join(bits)


def _map_render(docs, root_name, per, depth):
    """画一版地图。返回 (markdown, 目录数)。"""
    n_md = sum(1 for d in docs if d["类型"] == "md")
    n_html = sum(1 for d in docs if d["类型"] == "html")
    newest = max([d["时"] for d in docs] or [0])
    out = ["# 笔记库地图 · " + root_name,
           "生成于 %s · 共 %d 篇（md %d · html %d · 其他 %d）· 最近改动 %s"
           % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(docs), n_md, n_html,
              len(docs) - n_md - n_html,
              # 空库时 newest 是 0，别在这儿印一个 1970 年
              (_stamp(newest, "%Y-%m-%d %H:%M") if newest else "") or "—"),
           "用法：amnote search \"关键词\"；amnote read <路径> --section \"<标题>\"；"
           "amnote map --dir <目录> 展开某个目录。"]

    recent = sorted(docs, key=lambda d: -d["时"])[:MAP_RECENT]
    if recent:
        out += ["", "## 最近改动"]
        for d in recent:
            # 没有 `# 标题` 的（附件、没写标题的 md）退回文件名主干，
            # 别在这一行里把 `.csv` 再念一遍
            name = clean_title(d["路径"].rsplit("/", 1)[-1])
            out.append("- %s · %s · %s" % (_stamp(d["时"], "%Y-%m-%d %H:%M"),
                                           d["路径"], d["标题"] or name))

    groups = {}
    for d in docs:
        segs = [s for s in d["路径"].split("/")[:-1] if s]
        if depth:
            segs = segs[:depth]                  # 更深的折进这一层，文件名带上剩下的路径
        groups.setdefault("/".join(segs), []).append(d)

    out += ["", "## 目录"]
    roots = sorted(groups.pop("", []), key=lambda x: -x["时"])   # 根目录的先列
    for d in roots[:per]:
        out.append("- " + _map_line(d, ""))
    if len(roots) > per:
        out.append("- …及另外 %d 篇（amnote map --max %d）"
                   % (len(roots) - per, MAP_MAX_CAP))
    n_dirs = 0
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda x: -x["时"])
        n_dirs += 1
        out.append("- %s/（%d 篇）" % (key, len(items)))
        for d in items[:per]:
            out.append("  - " + _map_line(d, key))
        if len(items) > per:
            out.append("  - …及另外 %d 篇（amnote map --dir %s）"
                       % (len(items) - per, key))
    return "\n".join(out) + "\n", n_dirs


def map_rows(con):
    """地图要的那几列。门户和命令行离线都查这一句，列的顺序也就统一了。"""
    return con.execute("SELECT 路径,类型,mtime,大小,substr(正文,1,4000) "
                       "FROM 文档").fetchall()


def map_text(rows, root_name, sub="", per=MAP_MAX, depth=0, budget=MAP_BUDGET):
    """库地图的响应（§1.6 的形状）。`rows` 是 map_rows() 那五列。

    超预算就按 MAP_STEPS 把每个目录列的篇数减半重画；减到 2 篇还超，
    整体截断——一份读不完的地图不如一份有边界的。
    """
    sub = (sub or "").strip().strip("/")
    docs = []
    for rel, kind, mt, size, head in rows:
        if rel.rsplit("/", 1)[-1] in MAP_SKIP:
            continue
        if sub and not _in_dir(rel, sub):
            continue
        head = head or ""
        title = (first_heading(head) if kind == "md"
                 else html_title(head) if kind == "html" else "")
        docs.append({"路径": rel, "类型": kind, "时": mt or 0, "大小": size or 0,
                     "标题": title,
                     "一句话": _map_gist(head, kind, title)})
    text, n_dirs = _map_render(docs, root_name, per, depth)
    if len(text) > budget:
        for step in MAP_STEPS:
            if step >= per:
                continue
            text, n_dirs = _map_render(docs, root_name, step, depth)
            if len(text) <= budget:
                break
    cut = len(text) > budget
    if cut:
        text = text[:budget].rsplit("\n", 1)[0] + \
            "\n…（超出预算，地图截断了。用 --dir 一个目录一个目录地看）\n"
    return {"ok": True, "地图": text, "篇数": len(docs), "目录数": n_dirs,
            "截断": cut, "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M")}


def _mtime_text(mt):
    """索引里的 mtime → "%Y-%m-%d %H:%M:%S"。跟 /__meta 的「改于」一个格式。"""
    try:
        return datetime.fromtimestamp(mt or 0).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return ""


def since_ts(since):
    """`since` 参数 → 时间戳下限。空的返回 None（＝不筛）。

    ≤ 4 位的纯数字是天数（`7`＝最近七天）；再长的按日期读，`20260901` 和
    `2026-09-01` 都认。**读不懂就抛 ValueError**：`since=20260901` 原来会被
    当成「最近两千万天」，等于没筛，调用方和用户都看不出参数打错了。
    """
    s = str(since if since is not None else "").strip()
    if not s:
        return None
    if s.isdigit() and len(s) <= 4:
        return time.time() - int(s) * 86400
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    raise ValueError("since: %s" % s[:40])


def _in_dir(rel, sub):
    return rel == sub or rel.startswith(sub + "/")


def search(q, limit=60, offset=0, subdir="", types=None, since=None, sort="score"):
    """正文 ＋ 文件名子串搜索。多个词（空格隔开）是 AND。

    5.6 加了筛选和翻页（`offset` / `subdir` / `types` / `since` / `sort`），
    每条多给 改于 / 分数 / 大小，片段多给 行 / 小节。**默认参数下的结果和排序
    跟 5.5 逐条一样**——门户只传 q 和 n，那条路不能变。

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

    返回 [{路径, 类型, 命中数, 备注, 片段, 档, 改于, 分数, 大小}]。
    命中数 是「正文命中 ＋ 文件名命中」：只靠文件名中的那些，
    报 0 次命中会像是坏了。

    **分数和排序不是同一个东西。** 第一档（文件名全中）内部照旧按改动时间排——
    文件名打全了要的就是那一份；但把 mtime 那个十位数当「分数」发出去没有意义，
    所以 分数 一律是那条加权分，第一档另加 10 分，档次在数上也看得出来。
    """
    terms = [t for t in (q or "").split() if t]
    off = max(0, int(offset or 0))
    sub = (subdir or "").strip().strip("/")
    kinds = sorted(set(k.strip().lower() for k in (types or []) if str(k).strip()))
    floor = since_ts(since)                  # 读不懂会抛 ValueError，调用方接住
    if not terms:
        return {"ok": True, "状态": index_status(), "结果": [],
                "总命中": 0, "偏移": off}
    con = connect()
    st = index_status(con)
    # 筛选条件全部下沉到 SQL。原来是把命中的**每一行连正文**取回来再在
    # Python 里丢掉，`type=md&since=7` 这种查询等于白读几十 MB 正文
    clauses = ["(正文 LIKE ? ESCAPE '\\' OR 路径 LIKE ? ESCAPE '\\')"] * len(terms)
    args = []
    for t in terms:
        pat = "%" + _like_esc(t) + "%"
        args += [pat, pat]
    if floor is not None:
        clauses.append("mtime >= ?")
        args.append(floor)
    if kinds:
        clauses.append("lower(类型) IN (%s)" % ",".join("?" * len(kinds)))
        args += kinds
    if sub:
        # 前缀比对用 substr 不用 LIKE：SQLite 的 LIKE 对 ASCII 是**不分大小写**的，
        # 换成 LIKE 会让 `dir=notes` 连 `Notes/` 一起捞进来——跟原来那句
        # `rel.startswith(sub + "/")` 不是一个意思
        clauses.append("(路径 = ? OR substr(路径, 1, ?) = ?)")
        args += [sub, len(sub) + 1, sub + "/"]
    where = " AND ".join(clauses)
    rows = con.execute(
        f"SELECT 路径,类型,正文,备注,mtime,大小 FROM 文档 WHERE {where}", args).fetchall()
    con.close()

    lows = [t.lower() for t in terms]
    now = time.time()
    out = []
    for rel, kind, body, note, mt, size in rows:
        body = body or ""
        low = body.lower()
        name = rel.rsplit("/", 1)[-1].lower()
        folder = rel.lower().rsplit("/", 1)[0] if "/" in rel else ""
        n_body = sum(low.count(t) for t in lows)
        n_name = sum(name.count(t) for t in lows)
        # 一个月内的新鲜度接近满分，半年前掉到七分之一
        fresh = 1.0 / (1.0 + max(0.0, now - (mt or 0)) / (30 * 86400))
        score = (math.log1p(n_body)
                 + (2.0 if n_name else 0.0)
                 + (0.6 if any(t in folder for t in lows) else 0.0)
                 + 1.2 * fresh)
        if all(t in name for t in lows):
            tier, rank, score = 1, (mt or 0), score + 10.0   # 第一档按改动时间排
        else:
            tier, rank = 2, score
        out.append({"路径": rel, "类型": kind, "命中数": n_body + n_name,
                    "备注": note, "档": tier, "改于": _mtime_text(mt),
                    "分数": round(score, 2), "大小": size or 0,
                    "_排": rank, "_时": mt or 0, "_正文": body})
    if str(sort or "").lower() == "mtime":
        out.sort(key=lambda x: -x["_时"])
    else:
        out.sort(key=lambda x: (x["档"], -x["_排"]))
    total = len(out)
    # 片段是整份正文扫一遍，只给要返回的那几条算。n 上限从 200 提到 500 之后，
    # 给全部命中都算一遍片段是白扫几百 MB
    out = out[off:off + limit]
    for h in out:
        body = h.pop("_正文")
        h["片段"] = snippets(body, terms,
                             heads=md_headings(body) if h["类型"] == "md" else None)
        h.pop("_排", None)
        h.pop("_时", None)
    return {"ok": True, "状态": st, "结果": out, "总命中": total, "偏移": off}


def index_status(con=None):
    own = con is None
    if own:
        if not os.path.exists(V().db_path):
            return {"状态": "未建库", "收录": 0, "上次同步": ""}
        con = connect()
    st = {"状态": "同步中" if V().state["运行中"] else "就绪",
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
