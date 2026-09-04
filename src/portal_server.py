#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AM·Note · 本地服务 v21  (2026-08-29)

在库根起一个只读的本地服务，另外开几个接口给门户和 Agent 用。
v20 是「结构精简」那一刀之后的样子：门户只干两件事——读库里的 md、写随手记，
所以服务这边把标签层、收件箱、转表、PWA 四摊东西整层撤了。

启动：先 fulltext.configure(root)（--root 或 AMNOTE_VAULT），再绑端口。
没给库根就 stderr 说明原因后退出。默认端口 8870–8900。

v21（界面与编辑改版，A 路）改了五处，其余一律照旧：
    ① 口令门禁：启动时生成一次性 token，写 TOKEN_FILE（0600）。所有 POST 和
       /__rescan 要 X-AMN-Token 头，不对 403。GET 只读路由不要 token——页面里的
       <img> 带不了自定义头。另外每个请求都校验 Host，带 Origin 的校验 Origin。
       **永不输出 CORS 头。**
    ② /portal 出页时把模板里的 __AMN_TOKEN__ 换成真 token，前端从 meta 里读。
    ③ /__save 从「只收随手记目录」放开到全库 md（判据见 _edit_ok）；贴图同步放开。
    ④ 留档节流：自动保存把保存频次拉到停笔 2 秒一次，每次都留档会把 10 版名额
       几分钟就轮空，改成按 BACKUP_MIN_GAP 节流，两个例外必须留（见 _backup）。
    ⑤ 库外文档只读三件套 /__extopen /__extdoc /__extasset。**不写任何文件。**
       写库内文件的路由仍然只有 /__save 一条。

── 门户和 Agent 共用 ───────────────────────────────
    /__tree      目录树 ＋ 全部 md/html 文档 ＋ 随手记。**数据源是
                 .amnote/fulltext.db 的「文档」表**（v20 前是 scan_tags.py 生成的
                 产出清单.json，那个脚本已退役）。只读。
    /__search    全文搜索，带命中片段。只读。
    /__meta      一份文件的 路径 / 类型 / 标题 / 改于 / 大小。只读。
    /__raw       原样返回 md 源码，给随手记编辑器用。只读。
    /__changes   变更流水原文（按序号增量取）。开工快照也在用。只读。
    /__archive   读一份 .amnote/backups/ 里的留档。只读。
    /__current   门户此刻开着哪份文件。只读（内存态）。
    /__pulse     全库指纹（文件数 ＋ 最新修改时间）。门户每 3 秒问一次。
                 **响应字段一个都不许加**：前端把整串响应当指纹比对，
                 多一个会变的字段就是「扫完→指纹变→再扫」的死循环。
    /__status    服务状态。字段：ok, 状态, 端口, 库根, 上次扫描, 索引, 门禁,
                 随手记目录。前端有四处要「库根」的绝对路径（拖文件出去的
                 file:// URI、反解拖进来的文件、报给外壳），原来从烘进页面
                 的数据里拿，v20 起页面不再烘数据，只能从这里取。
    /__rescan    **POST**（v21 起；GET 打它返回 405）。触发一次全库同步，同步完
                 **返回和 /__tree 一模一样的对象**。设置面板那颗「重扫全库」和
                 pulse 指纹变了之后的自动重扫都走它，前端只写一个解析函数。
    /__config    GET 读配置，POST 写配置。可编辑键见 EDITABLE。
    /__state     POST，门户上报当前状态。只动内存。
    /__reveal    POST，在访达里选中这份文件。
    /__external  POST，交给系统默认程序打开（html 就是 Chrome）。      ← v20

    /__extopen   POST，登记一份**库外**的 md，返回 id。只进内存，重启即清。 ← v21
    /__extdoc    GET ?id=，读那份库外 md 的原文。要 token。            ← v21
    /__extasset  GET ?id=&rel=，读那份 md 同目录下的图片。要 token。    ← v21
                 这三条**一个字节都不写**，纯只读。Agent 不要用——库外的东西
                 不进索引、不进流水，Agent 该走文件系统。

    **剪贴板不在这里。** v20 一度开过 POST /__clip 走 pbcopy，v21 撤了：
    剪贴板是界面的事，客户端自己就有原生 API（壳走 NSPasteboard，浏览器走
    navigator.clipboard），没必要为一次拷贝起一个子进程。

    附一条排查坑：**别用 `pbpaste` 验剪贴板内容**。本机若把
    `~/.CFUserTextEncoding` 设成 `0x2`（MacChineseTrad），`pbpaste` 输出时照它转码，
    好好的 UTF-8 也会打印成 Big5 乱码。要验用原生 API 读回来。
    /__save      POST，写 md（含随手记）。见下。

    /portal      → 同目录 template.html。
                 模板里的 /*__DATA__*/null/*__DATA__*/ 占位符原样留着不注入，
                 前端读到 null 就全部走 /__tree。改前端不用再重扫，刷新即新版。
    /icon-192.png /icon-512.png → 脚本同目录下的同名文件

只绑 127.0.0.1，不对外。

**写库内文件的路由只剩 /__save 一条。** 放开到全库 md。换来的保险是口令门禁——
没有 token 打不进这条路由。三道老保险照旧：
    ① 写前把旧内容留进 .amnote/backups/，每份留最近 10 版（v21 起按时间节流）；
    ② 先写临时文件再 os.replace，中途断电不会留半截文件；
    ③ 打开编辑之后这份在别处被改过的，先拦一次，要前台再确认。
挡住的位置见 _edit_ok：备份目录、缓存目录、隐藏目录一律不给写。

v20 撤掉的路由（代码已删）：
    /__sheet、/__untagged、POST /__tag、/__backlinks、/__deadlinks、
    /__inbox、POST /__inbox_read、/manifest.json、POST /__open。
"""

import base64
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import OrderedDict
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 库根、配置、跳过／噪声规则、全文索引、变更流水、留档全在 fulltext 里。
# v20 起它是唯一的数据层——scan_tags / prep_batches / apply_tags / sheet_read
# 四个模块整层退役，收录口径不再有第二份。
import fulltext

ROOT = None
REAL_ROOT = None
CONFIG_PATH = None
BACKUP_DIR = None
TEMPLATE_HTML = os.path.join(HERE, "template.html")

# /portal 不在这张表里：v21 起出页要替换 token，走 Handler._portal_page
ALIAS = {
    "/icon-192.png": (os.path.join(HERE, "icon-192.png"), "image/png"),
    "/icon-512.png": (os.path.join(HERE, "icon-512.png"), "image/png"),
}


def _bind_vault():
    """configure 之后把本模块的库根、配置、备份目录跟 fulltext 对齐。"""
    global ROOT, REAL_ROOT, CONFIG_PATH, BACKUP_DIR
    ROOT = fulltext.ROOT
    REAL_ROOT = os.path.realpath(ROOT)
    CONFIG_PATH = fulltext.CONFIG_PATH
    BACKUP_DIR = fulltext.BACKUP_DIR


def cfg():
    """每次现读，改完 config.json 不用重启服务。"""
    c, _ = fulltext.load_config(CONFIG_PATH)
    return c


def note_dir():
    """随手记相对库根的目录。从配置读，空了回退默认值。"""
    d = cfg().get("随手记目录")
    if isinstance(d, str) and d.strip():
        return d.strip()
    return "随手记"


# ── 口令门禁 ──────────────────────────────────────
#
# 服务只绑 127.0.0.1，但「本机」不等于「只有门户」：这台机器上任何一个网页
# 都能往 127.0.0.1 上的端口发跨站请求。v20 时写路由只能碰随手记，敞口小；v21 把
# /__save 放开到全库 md 之后，必须有一道门。
#
# 三层，缺一不可：
#   · token —— 一次性随机口令，写进 TOKEN_FILE（0600，只有本人读得到），
#     /portal 出页时注进页面。所有 POST 和 /__rescan 校验 X-AMN-Token 头。
#     **GET 只读路由不要 token**：页面里的 <img src> 带不了自定义头，要了就
#     等于把库内图片全部渲染不出来。库外那两条 GET 是例外，前端用 fetch 取。
#   · Host —— 必须是 127.0.0.1:<port> 或 localhost:<port>。挡的是 DNS rebinding：
#     一个外部域名把自己解析到 127.0.0.1，浏览器发过来的 Host 是那个域名。
#   · Origin —— 带了就必须是自己。挡的是别的页面发来的跨站表单/fetch。
# 再加一条：**永不输出 CORS 头**，浏览器那边读不走响应。

TOKEN_FILE = os.path.expanduser(
    os.environ.get("AMN_TOKEN_FILE")
    or "~/Library/Application Support/AMNote/portal.token")
TOKEN = secrets.token_hex(32)                 # 32 字节 → 64 个 hex 字符
TOKEN_PLACEHOLDER = "__AMN_TOKEN__"           # 模板里的占位串，出页时替换


def write_token_file(path=None):
    """把这一趟的 token 写给本机用（壳、curl 要发 POST 时读它）。

    权限锁 0600。**不能只靠 os.open 的 mode**：文件已经存在时 O_CREAT 的 mode
    不生效，上一趟留下的宽权限会一路继承下来，所以写完再 chmod 一次。
    """
    path = path or TOKEN_FILE
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(TOKEN)
        os.chmod(path, 0o600)
    except OSError as e:
        print(f"口令文件写不了（{path}）：{e}", file=sys.stderr, flush=True)
        return ""
    return path


def token_ok(got: str) -> bool:
    return hmac.compare_digest(got or "", TOKEN)


_lock = threading.Lock()
_cache = {"fp": None, "at": 0.0}
_state = {"port": 0, "started": time.time()}


def fingerprint():
    """全库指纹：md/html ＋ 附件的文件数和最新 mtime。任一变了就说明库里有动静。
    附件也算，不然新拖进来一份 pdf 要等到手动重扫才进索引。"""
    n = 0
    newest = 0.0
    c = cfg()
    skip = tuple(c["跳过目录关键词"]) + tuple(c["噪声目录"])
    exts = (".md", ".html", ".htm") + tuple(fulltext.ATT_EXT)
    for dp, dn, fns in os.walk(ROOT):
        dn[:] = [d for d in dn
                 if d != ".amnote" and not any(t in d for t in skip)]
        for fn in fns:
            if fn.startswith((".", "_", "~$")):
                continue
            if not fn.lower().endswith(exts):
                continue
            full = os.path.join(dp, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            n += 1
            if st.st_mtime > newest:
                newest = st.st_mtime
    return {"文件数": n, "最新改动": round(newest, 1)}


def cached_fingerprint(max_age=2.0):
    with _lock:
        now = time.time()
        if _cache["fp"] is None or now - _cache["at"] > max_age:
            _cache["fp"] = fingerprint()
            _cache["at"] = now
        return _cache["fp"]


# ── 标题与预览 ────────────────────────────────────
#
# v20 起标题不再从标签块里读（标签层整层退役，库里 900 多份 md 的标签块一个字
# 不动，只是门户不再读它）。推导顺序：md 取正文第一个 `# ` 标题；html 取抽取
# 正文的第一行（_extract_html 把 <title> 放在最前）；都取不到再退回文件名主干。

TAG_HEAD_RE = re.compile(r"^\s*---.*?\n---\s*", re.S)


def first_heading(text: str) -> str:
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
        return s[2:].strip().strip("#").strip()
    return ""


def clean_title(fn: str) -> str:
    """文件名主干：剥掉 _vN 和八位日期，下划线换空格。库里的命名规矩是
    `名称_vN_YYYYMMDD.md`，那两截在列表里另有一格，标题里再写一遍是重复。"""
    t = os.path.splitext(fn)[0]
    t = re.sub(r"_v[\d.]+(?=_|$)", "", t, flags=re.I)
    t = re.sub(r"_?20\d{6}(?=_|$)", "", t)
    return t.replace("_", " ").strip(" ·-—") or os.path.splitext(fn)[0]


def disambiguate(title: str, rel: str, generic) -> str:
    """README / 00_索引 这类通用名，前面补上所在项目，否则列表里一堆同名分不清。"""
    if title.strip() not in generic:
        return title
    # 往上找一个有信息量的目录名，跳过 01_ 02_ 这种纯编号壳
    for seg in reversed(rel.replace(os.sep, "/").split("/")[:-1]):
        clean = re.sub(r"^\d+[、_.-]\s*", "", seg).strip()
        if len(clean) >= 2:
            return f"{clean} · {title.strip()}"
    return title


def html_title(head: str) -> str:
    """html 抽取正文把 <title> 放在第一行。太长就当正文，不当标题。"""
    if not head:
        return ""
    line = head.split("\n", 1)[0].strip()
    if not line or len(line) > 80:
        return ""
    return line


def title_of(rel: str, head: str, kind: str, generic) -> str:
    if kind == "md":
        t = first_heading(head)
    elif kind == "html":
        t = html_title(head)
    else:
        t = ""
    return disambiguate(t or clean_title(os.path.basename(rel)), rel, generic)


def list_preview(head: str, kind: str) -> str:
    """列表和本地搜索用的短摘录。html 已经是抽过的纯文本。"""
    s = head or ""
    if kind == "md":
        s = TAG_HEAD_RE.sub("", s, count=1)
        s = re.sub(r"^#+\s*", "", s, flags=re.M)
        s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:240]


def preview(raw: str, n=200) -> str:
    """随手记的一行预览。先剥标签块、表格分隔行和贴图，剥完还看得出这篇在讲什么。
    只给随手记用——列表上光有标题等于没给，预览这一句才是认出「哪篇是哪篇」的凭据。"""
    out = []
    for ln in TAG_HEAD_RE.sub("", raw or "", count=1).splitlines():
        s = ln.strip()
        if not s or set(s) <= set("|-: "):        # 表格分隔行
            continue
        out.append(s.lstrip("#").strip())
    # 贴图落成 ![](_图/xxx.png)，剥标签块剥不掉它，预览里会是一串路径
    txt = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", " ".join(out))
    return re.sub(r"\s+", " ", txt).strip()[:n]


# ── 路径校验 ──────────────────────────────────────

def _view_full(rel: str):
    """只读用的放行版：md/html 之外，pdf 和表格这些附件也认。
    给「看」「在访达里选中」「交给系统打开」「拷路径」用——
    写文件那条路走 _edit_ok，只认 md，附件一律不给写。"""
    if not rel or rel.startswith("/") or "\x00" in rel:
        return None, "路径不合法"
    full = os.path.realpath(os.path.join(ROOT, rel))
    if not (full == REAL_ROOT or full.startswith(REAL_ROOT + os.sep)):
        return None, "路径越出库根"
    if not os.path.isfile(full):
        return None, "文件不在了"
    if not full.lower().endswith((".md", ".html", ".htm") + tuple(fulltext.ATT_EXT)):
        return None, "这个格式门户不认"
    return full, ""


# ── 访达 ／ 系统默认程序 ／ 剪贴板 ─────────────────

def reveal(req: dict):
    """在访达里选中这份文件。只是打开一个窗口，不动文件。"""
    full, err = _view_full((req.get("路径") or "").strip())
    if err:
        return {"ok": False, "错误": err}
    try:
        subprocess.run(["open", "-R", full], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return {"ok": False, "错误": f"打不开访达：{e}"}
    return {"ok": True, "路径": full}


def open_external(req: dict):
    """交给系统默认程序打开。pdf / xlsx 走这条；html 默认在标签里读，
    「在浏览器里打开」才落到这里。

    走「系统默认」而不是写死 Chrome：这台机器上 https 和 public.html 两个默认
    处理程序都是 com.google.chrome（20260825 实测），效果一样，以后换浏览器
    也不用改代码。
    """
    full, err = _view_full((req.get("路径") or "").strip())
    if err:
        return {"ok": False, "错误": err}
    try:
        subprocess.run(["open", full], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return {"ok": False, "错误": f"打不开：{e}"}
    return {"ok": True, "路径": full}


def read_raw(rel: str):
    """编辑器要的是源码，原样给。渲染那条路走静态服务，不走这里。"""
    full, err = _view_full(rel)
    if err:
        return {"ok": False, "错误": err}
    if not full.endswith(".md"):
        return {"ok": False, "错误": "只有 md 能在门户里读源码"}
    try:
        with open(full, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "错误": f"读不了：{e}"}
    return {"ok": True, "路径": rel, "正文": text,
            "字节": os.path.getsize(full),
            "改于": datetime.fromtimestamp(
                os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M:%S")}


# ── 编辑：新建、贴图、保存 ──────────────────────────────────────────
#
# 三件事都挂在 /__save 上，不新开路由。但各写各的函数，不去搅 save_md 里那套
# 覆写保护（留备份、比对修改时间）：那套是为「盖掉已有内容」设计的，新建和写图
# 一条都用不上，塞进同一个函数只会让保护逻辑多几个绕过分支。
#
# **写入范围 v21 从随手记放开到全库 md**，判据集中在 _edit_ok 一处。
# 前端的 canEditPath 要跟这里逐条对齐，两边改必须一起改。

BACKUP_KEEP = 10                      # 每份文件留最近这么多版
BACKUP_MIN_GAP = 600                  # 同一份文件两次留档至少隔这么多秒（10 分钟）
IMG_SUB = "_图"
IMG_MAX = 12_000_000                  # 单张 12 MB。截图撑死几 MB，留足余量

# 路径里出现这几段就不给写。都是工具自己的地盘，不是产出：
# _编辑备份＝旧留档目录名，__pycache__＝字节码，
# .amnote＝库元数据目录（段以点开头的也会挡住）。用「包含」不用「相等」。
BLOCK_SEG = ("_编辑备份", "__pycache__", ".amnote")


def _seg_blocked(seg: str) -> bool:
    return seg.startswith(".") or any(t in seg for t in BLOCK_SEG)


def _edit_ok(rel: str):
    """可编辑 md 的判据。**这是「门户能写哪儿」的唯一定义**，前端照抄一份。

    放行：库根之内、以 .md 结尾、路径每一段都不是隐藏目录 / 备份 / 缓存。

    段判两遍：先判请求里的相对路径，realpath 之后按实际落点再判一遍。
    只判前者的话，一条指向 .amnote/backups/ 的软链接就绕过去了。
    """
    if not rel or rel.startswith("/") or "\x00" in rel or ".." in rel.split("/"):
        return None, "路径不合法"
    if not rel.endswith(".md"):
        return None, "只有 md 能在门户里改"
    for seg in rel.split("/"):
        if not seg or _seg_blocked(seg):
            return None, "这个位置不给写"
    full = os.path.realpath(os.path.join(ROOT, rel))
    if not full.startswith(REAL_ROOT + os.sep):
        return None, "路径越出库根"
    for seg in os.path.relpath(full, REAL_ROOT).split(os.sep):
        if _seg_blocked(seg):
            return None, "这个位置不给写"
    return full, ""


def _edit_full(rel: str, must_exist: bool):
    full, err = _edit_ok(rel)
    if err:
        return None, err
    if must_exist and not os.path.isfile(full):
        return None, "文件不在了"
    if not must_exist and os.path.exists(full):
        return None, "同名文件已经有了"
    return full, ""


def _last_backup(flat: str):
    """这份文件最近一次留档的 (文件名, 写入时间)。没有留档返回 (None, 0)。

    文件名里的时间戳是 %Y%m%d-%H%M%S-%f，字典序就是时间序，排完取最后一个。
    """
    try:
        olds = sorted(fn for fn in os.listdir(BACKUP_DIR)
                      if fn.startswith(flat + "__") and fn.endswith(".bak"))
    except OSError:
        return None, 0.0
    if not olds:
        return None, 0.0
    try:
        return olds[-1], os.path.getmtime(os.path.join(BACKUP_DIR, olds[-1]))
    except OSError:
        return olds[-1], 0.0


def _backup(rel: str, old: str, force: bool = False) -> str:
    """写前留档。文件名把路径压平，同一份的历史版排在一起，按时间截断。
    返回留档文件名；跳过或写不了返回 ''。

    备份放在 .amnote/backups/，这个目录名在「跳过目录关键词」里，
    索引不收，不会自己列自己。

    **v21 起按时间节流。** 自动保存是停笔 2 秒落一次盘，改一段话就是三五次
    保存；每次都留档的话，10 版名额几分钟就轮空，真正想找回的「今天上午那一版」
    反而被自己挤掉了。所以同一份文件距上次留档不足 BACKUP_MIN_GAP 秒就跳过。
    两种情况必须留，跟节流无关：
      · force —— 这份在编辑期间被别处改过，这次是强存，会盖掉那次改动；
      · 一版留档都还没有 —— 第一版最要紧，丢了就没有回头路。
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # 压平规则跟 fulltext.archive_text 共用一份：两边写的是同一个目录、同一套
    # 前缀，各写各的话哪天改了一边，同一份文件的历史版就散成两串了
    flat = fulltext._flat(rel)
    last, last_at = _last_backup(flat)
    if last and not force and time.time() - last_at < BACKUP_MIN_GAP:
        return ""
    # 精确到毫秒。只精确到秒的话，同一秒里连存两次，后一次会把前一次的备份盖掉
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    name = f"{flat}__{stamp}.bak"
    try:
        with open(os.path.join(BACKUP_DIR, name), "w", encoding="utf-8") as f:
            f.write(old)
    except OSError:
        return ""                                # 备份写不了不该拦住正常保存
    olds = sorted(fn for fn in os.listdir(BACKUP_DIR)
                  if fn.startswith(flat + "__") and fn.endswith(".bak"))
    for fn in olds[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, fn))
        except OSError:
            pass
    return name


def _sniff_img(b: bytes):
    """认头几个字节定格式。不看前端报的 MIME——那是前端说了算的东西。"""
    if b.startswith(b"\x89PNG\r\n\x1a\n"):        return "png"
    if b.startswith(b"\xff\xd8\xff"):              return "jpg"
    if b[:6] in (b"GIF87a", b"GIF89a"):             return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":     return "webp"
    return ""


def new_md(req: dict):
    """新建一份 md。只建新的，绝不覆盖已有的。"""
    rel = (req.get("路径") or "").strip()
    body = req.get("正文")
    if not rel.endswith(".md"):
        return {"ok": False, "错误": "只能新建 md"}
    if not isinstance(body, str) or not body.strip():
        return {"ok": False, "错误": "正文不能是空的"}
    if len(body.encode("utf-8")) > 1_000_000:
        return {"ok": False, "错误": "新建时正文别超过 1 MB"}
    full, err = _edit_full(rel, must_exist=False)
    if err:
        return {"ok": False, "错误": err}
    # **门户不替用户在库里造目录。** 新建放开到全库之后，随手写个名字就能
    # 长出一层新文件夹。只有随手记目录例外——第一次用时得让它自己长出来
    parent = os.path.dirname(full)
    if not os.path.isdir(parent):
        if not rel.startswith(note_dir() + "/"):
            return {"ok": False, "错误": "这个文件夹还不存在，先在访达里建好"}
    try:
        os.makedirs(parent, exist_ok=True)
        note_portal_write(rel)                   # 同 save_md：记账赶在文件出现之前
        # "x" 模式：文件已存在就抛。跟上面的检查重了一道，防的是连点两下撞车
        with open(full, "x", encoding="utf-8") as f:
            f.write(body)
    except FileExistsError:
        return {"ok": False, "错误": "同名文件刚被建走了，换个标题"}
    except OSError as e:
        return {"ok": False, "错误": f"写不进去：{e}"}
    return {"ok": True, "路径": rel, "字节": len(body.encode("utf-8"))}


def save_img(req: dict):
    """把粘贴进来的图片写进那份 md 旁边的 _图/。v21 起随手记之外的 md 也能贴。

    文件名一律服务端按时间戳生成，不收前端给的名——收了就等于把「往库里
    写任意文件名」这个能力交出去了。
    """
    rel = (req.get("路径") or "").strip()          # 那份 md 的相对路径
    raw64 = (req.get("图片") or {}).get("数据") or ""
    md_full, err = _edit_full(rel, must_exist=True)
    if err:
        return {"ok": False, "错误": err}
    if len(raw64) > IMG_MAX * 4 // 3 + 1024:       # base64 撑大 4/3，先卡一道免得白解
        return {"ok": False, "错误": f"这张超过 {IMG_MAX // 1024 // 1024} MB 了"}
    try:
        blob = base64.b64decode(raw64, validate=True)
    except Exception:
        return {"ok": False, "错误": "图片数据不对"}
    if not blob:
        return {"ok": False, "错误": "图片是空的"}
    if len(blob) > IMG_MAX:
        return {"ok": False,
                "错误": f"这张 {len(blob) // 1024 // 1024} MB，上限 {IMG_MAX // 1024 // 1024} MB"}
    ext = _sniff_img(blob)
    if not ext:
        return {"ok": False, "错误": "只收 png / jpg / gif / webp"}

    d = os.path.join(os.path.dirname(md_full), IMG_SUB)
    name = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + "." + ext
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "xb") as f:
            f.write(blob)
    except OSError as e:
        return {"ok": False, "错误": f"图片写不进去：{e}"}
    # 返回相对这份 md 的路径，前端照原样写进 markdown，渲染时按 md 所在目录解
    return {"ok": True, "相对路径": f"{IMG_SUB}/{name}", "字节": len(blob)}


def _mtime_str(full: str) -> str:
    """一份文件的落盘时间。**格式必须跟 /__meta 的「改于」逐字一样**：
    前端把它原样存下来，下一次保存当「基于」送回来，跟这里算出的 now 比对。
    两边格式差一个字，每一次自动保存都会变成一次假冲突。"""
    try:
        return datetime.fromtimestamp(
            os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return ""


def _rename_md(rel: str, new_rel: str):
    """保存成功后把文件改个名。只改文件名，不换目录。

    失败不回滚正文——调用方已经写完了。返回 (实际路径, 错误)；错误为空串即成功。
    """
    new_rel = (new_rel or "").strip()
    if not new_rel or new_rel == rel:
        return rel, ""
    if os.path.dirname(new_rel) != os.path.dirname(rel):
        return rel, "只能改文件名"
    dest, err = _edit_full(new_rel, must_exist=False)
    if err:
        return rel, err
    src, err = _edit_full(rel, must_exist=True)
    if err:
        return rel, err
    try:
        note_portal_write(new_rel)
        os.rename(src, dest)
    except OSError as e:
        return rel, f"改名失败：{e}"
    return new_rel, ""


def save_md(req: dict):
    """整篇覆写一份 md。

    成功响应带「改于」＝落盘后的 mtime，前端自动保存完直接拿它更新「基于」，
    不用再补打一次 /__meta。
    """
    rel = (req.get("路径") or "").strip()
    body = req.get("正文")
    force = bool(req.get("强制"))

    full, err = _edit_full(rel, must_exist=True)
    if err:
        return {"ok": False, "错误": err}
    if not isinstance(body, str):
        return {"ok": False, "错误": "正文得是文本"}
    if len(body.encode("utf-8")) > 8_000_000:
        return {"ok": False, "错误": "这份太大了（超过 8 MB），别在门户里改"}

    try:
        with open(full, encoding="utf-8") as f:
            old = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "错误": f"读不了原文：{e}"}

    if old == body:
        return {"ok": True, "结果": "没有改动", "路径": rel,
                "字节": {"写前": len(old.encode()), "写后": len(old.encode())},
                "改于": _mtime_str(full)}

    # 编辑期间这份被别处改过（另一个标签、编辑器、外包脚本）→ 先拦一次，别默默盖掉
    based = (req.get("基于") or "").strip()
    now = _mtime_str(full)
    clash = bool(based and based != now)
    if clash and not force:
        return {"ok": False, "需确认": True,
                "错误": f"你打开编辑之后，这份在别处被改过（{now}）。继续保存会盖掉那次改动。"}

    # 走到这里还 clash＝按了「保留我的」强存，这一版要盖掉别人的改动，
    # 不管节流窗口一律留档
    bak = _backup(rel, old, force=clash)
    tmp = full + ".amnote-tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, os.stat(full).st_mode & 0o7777)
        # **记账要赶在文件露出新 mtime 之前。** 记在写完之后的话，中间那一瞬
        # 正好有一趟后台同步扫到这份，活表里还没有这一笔，就会把这次保存
        # 判成「外部」——白留一版档，流水上也记错来源
        note_portal_write(rel)
        os.replace(tmp, full)                    # 同盘改名是原子的，不会留半截文件
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "错误": f"写失败：{e}"}

    new_rel = (req.get("新路径") or "").strip()
    if new_rel:
        rel, _ = _rename_md(rel, new_rel)
        full, err2 = _edit_ok(rel)
        if err2 or not full:
            full = os.path.realpath(os.path.join(ROOT, rel))

    return {"ok": True, "结果": "已保存", "路径": rel,
            "字节": {"写前": len(old.encode()), "写后": len(body.encode())},
            "备份": os.path.relpath(BACKUP_DIR, ROOT).replace(os.sep, "/"),
            "留档": bak,                          # 空串＝这一次按节流跳过了
            "改于": _mtime_str(full)}             # 落盘后的 mtime，见函数上方那段


def save_route(req: dict):
    """/__save 一条路上的三件事，按请求里的字段分派。

    写入范围在这里统一卡一道：三条分支的「路径」都得先过 _edit_ok。
    下游三个函数各自还会再校验一次——这一道是给「一眼看清写入范围」用的，
    别为了不重复就把它删了。
    """
    rel = (req.get("路径") or "").strip()
    _, err = _edit_ok(rel)
    if err:
        return {"ok": False, "错误": err}
    if req.get("图片"):
        return save_img(req)
    if req.get("新建"):
        return new_md(req)
    return save_md(req)


# ── 配置 ──────────────────────────────────────────

# 设置面板只编辑这几项。板块名、以及老 config.json 里残留的
# 主题清单 / 目录默认主题 / 默认主题 / 索引文件（随标签体系退役），
# 写回时原样留着不动——门户不该顺手把配置文件里别的东西删了。
EDITABLE = ("跳过目录关键词", "噪声目录", "噪声文件", "通用标题",
            "端口范围", "随手记目录")


def read_config():
    c, problems = fulltext.load_config(CONFIG_PATH)
    raw = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
    return {"配置": c, "默认值": fulltext.DEFAULTS, "可编辑": list(EDITABLE),
            "问题": problems, "文件存在": os.path.exists(CONFIG_PATH),
            "状态": status(),
            "说明": {k: v for k, v in raw.items() if k.startswith("_")}}


def write_config(req: dict):
    """只认 EDITABLE 那几个键，其余一律忽略（不报错——本机可能还留着旧配置）。"""
    if not isinstance(req, dict):
        return {"ok": False, "错误": "配置得是一个对象"}
    out = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                out = json.load(f)
        except (OSError, ValueError):
            out = {}
    if not isinstance(out, dict):
        out = {}

    for k in EDITABLE:
        if k not in req:
            continue
        v = req[k]
        if not isinstance(v, type(fulltext.DEFAULTS[k])):
            return {"ok": False, "错误": f"「{k}」类型不对"}
        out[k] = v

    pr = out.get("端口范围") or fulltext.DEFAULTS["端口范围"]
    if not (isinstance(pr, list) and len(pr) == 2
            and all(isinstance(x, int) for x in pr)
            and 1 <= pr[0] <= pr[1] <= 65535):
        return {"ok": False, "错误": "端口范围要填两个 1-65535 的整数，前小后大"}

    try:
        os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, indent=2)
    except OSError as e:
        return {"ok": False, "错误": f"写失败：{e}"}
    _, problems = fulltext.load_config(CONFIG_PATH)
    return {"ok": True, "问题": problems}


def status():
    """服务状态。**字段只有这几个**：前端要的就是端口、库根和索引进度，
    v19 那批「产出数 / 核心数 / 登记数 / 待补标签数 / 问题数」全是标签层的账，
    随标签层一起撤了；「收件箱未读 / 待打开」两条队列也撤了。

    v21 加了「门禁」一个字段，给前端认版本用。**能加字段的是 status，不是
    pulse**——pulse 那串是被当指纹整串比对的，加一个字段就是死循环。"""
    try:
        s = fulltext.index_status()
        idx = {"收录": s.get("收录", 0), "状态": s.get("状态", "")}
        last = s.get("上次同步", "")
    except Exception:
        idx, last = {"收录": 0, "状态": "异常"}, ""
    return {"ok": True,
            "状态": "扫描中" if idx["状态"] == "同步中" else "就绪",
            "端口": _state["port"], "库根": ROOT,
            "上次扫描": last, "索引": idx, "门禁": True,
            "随手记目录": note_dir()}


# ── Agent 接口层 ──────────────────────────────────────────
#
# 全文索引、变更流水、留档的本体都在 fulltext.py，这里只是把它们挂成 HTTP 路由，
# 给门户前端和 Agent（curl）共用。这一层全部不碰库内产出。

_agent = {"门户状态": {}, "门户状态时间": 0.0}


def note_portal_write(rel):
    """门户里每写成功一笔（/__save），记下来。流水那边据此把这些改动标成「门户」
    而不是「外部」，别把用户自己的保存当成外部改动。

    活表本身在 fulltext 里（v21 起）。**这里不再往 sync 里传快照**：
    快照是在起线程那一刻拷的，撞上「已有一趟在跑」的补跑就会用旧的那份，
    把刚记的这笔漏掉、判成外部。判定改到 fulltext 里当刻加锁查活表。
    """
    fulltext.note_portal_write(rel)


def kick_sync():
    """后台补一轮全文同步（抽正文、记流水、留档）。fulltext.sync 自带锁，
    重复叫只会登记一次待重跑，不会叠着跑。"""
    threading.Thread(target=fulltext.sync, daemon=True).start()


_tree_cache = {"key": None, "data": None}


def _db_key():
    """索引库的指纹，当树缓存的键。

    **要连 -wal 一起看**：库是 WAL 模式，写入先落 fulltext.db-wal，
    主库文件的 mtime 要等 checkpoint 才动。只盯 .db 会一直返回过期的树。
    """
    out = []
    for p in (fulltext.DB_PATH, fulltext.DB_PATH + "-wal"):
        try:
            st = os.stat(p)
            out.append((round(st.st_mtime, 2), st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def tree_view():
    """目录树 ＋ 全部 md/html ＋ 随手记。边栏、列表、最近改动都吃这一份。

    **数据源是 fulltext.db 的「文档」表**。v20 前是 scan_tags.py 生成的
    产出清单.json，那条路要求文件先打标签才进得来，1187 份没打标签的挂在
    另一张表上，两批合起来才是「文件夹里有什么可读的」。索引没有这道门槛，
    收的就是全部，少一层账。

    只给 md 和 html。每条带 路径 / 标题 / 类型 / 改于 / 预览。
    「附件」那个键留着但恒为空数组：pdf / xlsx / csv 的文字
    照旧进索引搜得到，只是不在门户里列、也不在门户里渲染。

    目录树只把「装着文件的目录」列出来，空壳目录和被跳过的目录自动消失。
    份数是含子目录的累计数。缓存跟着索引库的指纹走，没同步过就不重算。
    """
    key = _db_key()
    with _lock:
        if _tree_cache["key"] == key and _tree_cache["data"]:
            return _tree_cache["data"]

    try:
        con = fulltext.connect()
        # 只取正文开头：标题在第一个 `# ` 里，整份 md 拉出来白读几 MB
        rows = con.execute("SELECT 路径,类型,mtime,substr(正文,1,4000) FROM 文档 "
                           "WHERE 类型 IN ('md','html')").fetchall()
        synced = fulltext.meta_get(con, "上次同步")
        con.close()
    except Exception as e:
        return {"ok": False, "错误": f"fulltext.db 读不了：{e}（跑一次重扫）"}

    generic = tuple(cfg().get("通用标题") or ())
    docs = [{"路径": rel, "标题": title_of(rel, head or "", kind, generic),
             "类型": kind, "改于": round(mt or 0, 1),
             "预览": list_preview(head or "", kind)}
            for rel, kind, mt, head in rows]
    docs.sort(key=lambda x: -x["改于"])

    # 每层目录的累计份数。a/b/c.md 要让 a 和 a/b 都加一
    n_dir = {}
    for r in docs:
        seg = r["路径"].split("/")[:-1]
        for i in range(len(seg)):
            n_dir["/".join(seg[:i + 1])] = n_dir.get("/".join(seg[:i + 1]), 0) + 1

    top = {}
    for k in n_dir:
        if "/" not in k:
            top.setdefault(k, [])
    for k in sorted(n_dir):
        if k.count("/") == 1:
            top.setdefault(k.split("/")[0], []).append(
                {"名称": k.split("/")[1], "份数": n_dir[k]})

    names = cfg().get("板块名") or {}
    tree = [{"名称": name, "显示名": names.get(name.split("、")[0]) or name,
             "份数": n_dir.get(name, 0), "子目录": top[name]}
            for name in sorted(top)]

    # 随手记单独给一份带预览的。就那几十份，现读现剥，不值得进索引那条路
    notes = []
    nd_rel = note_dir()
    nd = os.path.join(ROOT, nd_rel.replace("/", os.sep))
    try:
        fns = sorted(os.listdir(nd))
    except OSError:
        fns = []
    for fn in fns:
        if not fn.endswith(".md") or fn.startswith((".", "_")):
            continue
        full = os.path.join(nd, fn)
        try:
            st = os.stat(full)
            with open(full, encoding="utf-8", errors="replace") as f:
                raw = f.read(20000)
        except OSError:
            continue
        # 标题要在剥之前从原文里取：preview 会把 `# ` 一起削掉。
        # 早期随手记只有标签块、没有 `# ` 标题行，退回文件名
        notes.append({"路径": nd_rel + "/" + fn,
                      "标题": first_heading(raw) or clean_title(fn),
                      "改于": round(st.st_mtime, 1), "预览": preview(raw, 200)})
    notes.sort(key=lambda x: -x["改于"])

    out = {"ok": True, "目录": tree, "文档": docs, "附件": [], "随手记": notes,
           "根文档": sum(1 for r in docs if "/" not in r["路径"]),
           "总数": len(docs), "生成时间": (synced or "")[:16]}
    with _lock:
        _tree_cache["key"] = key
        _tree_cache["data"] = out
    return out


def rescan():
    """重扫：同步跑一轮，跑完返回和 /__tree 一模一样的对象。

    **是增量的，不是全库重建。** fulltext.sync 只碰 (mtime, 大小) 变过的文件，
    库里没动静时整趟 0.1 秒。这条被打得很频繁——门户每 3 秒问一次 /__pulse，
    指纹一变就调它，边写随手记边保存就会一直触发——全量重建撑不住这个频率。
    改了跳过 / 噪声规则也走这条：walk_files 每次现读 config.json，
    新进来的按「新增」处理、被排掉的按「删除」处理，不用全量。

    真正的全库重抽只有两种情况要做，都在命令行，不给按钮：换了索引截断参数
    （`fulltext.py --compact`），或者索引库整个删了重建（`--sync`）。

    同步跑不后台跑——前端点了「重扫」就是要等结果，后台跑会让界面看着旧列表
    以为没生效。fulltext.sync 自带锁，撞上正在跑的那趟会直接返回，
    这时给出的树是上一轮的，下一次 pulse 会再来一遍。
    """
    try:
        fulltext.sync()
    except Exception as e:
        return {"ok": False, "错误": f"{type(e).__name__}: {e}"}
    return tree_view()


def search_view(query):
    """全文搜索，给门户前端和 Agent 共用。正文命中怎么跟标题命中合着排，
    是前端 / 调用方的事；这里只给命中、片段和标题。

    **返回全部类型**（含 csv / xlsx / pdf）。门户那边只显示 md 和 html，
    但 Agent 找一份表格靠的就是这条，服务端不替它过滤。

    v21 起 n 默认 200、上限 500（原来是 60 / 200）：搜「的」这种字在库里命中
    七八百份，60 条截出来的那一段没有意义，前端要的是能一次滚完的一整批。
    """
    q = (query.get("q") or [""])[0]
    try:
        n = max(1, min(int((query.get("n") or ["200"])[0]), 500))
    except ValueError:
        n = 200
    r = fulltext.search(q, limit=n)
    by = {d["路径"]: d["标题"] for d in (tree_view().get("文档") or [])}
    for h in r.get("结果", []):
        h["标题"] = by.get(h["路径"]) or clean_title(os.path.basename(h["路径"]))
    return r


def meta_view(query):
    """一份文件的基本面。Agent 拿它代替自己去 stat ＋ 读文件头。"""
    rel = (query.get("path") or [""])[0]
    full, err = _view_full(rel)
    if err:
        return {"ok": False, "错误": err}
    low = full.lower()
    kind = ("md" if low.endswith(".md") else "html" if low.endswith((".html", ".htm"))
            else fulltext.ATT_EXT.get(os.path.splitext(low)[1], ""))
    head = ""
    if kind == "md":
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                head = f.read(20000)
        except OSError:
            pass
    out = {"ok": True, "路径": rel, "类型": kind,
           "标题": title_of(rel, head, kind, tuple(cfg().get("通用标题") or ()))}
    try:
        st = os.stat(full)
        out["改于"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        out["大小"] = st.st_size                  # 字节
    except OSError:
        out["改于"], out["大小"] = "", 0
    return out


def changes_view(query):
    """变更流水原样给出去，Agent 查「这几天库里动了什么」用。"""
    try:
        since = int((query.get("since") or ["0"])[0])
        n = max(1, min(int((query.get("n") or ["200"])[0]), 1000))
    except ValueError:
        since, n = 0, 200
    return {"ok": True, "流水": fulltext.journal_read(since, n)}


def archive_view(query):
    """读一份留档（门户编辑备份和外部覆写留档同一个目录）。只读。"""
    name = (query.get("name") or [""])[0]
    text = fulltext.archive_read(name)
    if text is None:
        return {"ok": False, "错误": "没有这份留档"}
    return {"ok": True, "名称": name, "正文": text}


def state_set(req):
    """门户前端把「此刻开着什么」报上来，存内存。Agent 问 /__current 时用。"""
    st = {}
    if isinstance(req.get("视图"), str):
        st["视图"] = req["视图"][:20]
    if isinstance(req.get("当前文件"), str):
        st["当前文件"] = req["当前文件"][:500]
    if isinstance(req.get("筛选"), dict):
        st["筛选"] = {str(k)[:20]: (str(v)[:80] if not isinstance(v, bool) else v)
                      for k, v in list(req["筛选"].items())[:12]}
    if isinstance(req.get("打开标签"), list):
        st["打开标签"] = [str(x)[:500] for x in req["打开标签"][:20]]
    _agent["门户状态"] = st
    _agent["门户状态时间"] = time.time()
    return {"ok": True}


def current_view():
    age = time.time() - _agent["门户状态时间"] if _agent["门户状态时间"] else None
    return {"ok": True, "门户开着": bool(_agent["门户状态"]) and (age or 9e9) < 30,
            "状态": _agent["门户状态"],
            "秒前": round(age, 1) if age is not None else None}


# ── 库外文档（外部文档模式）──────────────────────────────────
#
# 桌面上、下载里的一份 md，拖进门户就能读。**只读，一个字节都不写**：
# 不进索引、不进流水、不进最近、不能编辑、不能贴图。
#
# 为什么要登记一张内存表，不直接收绝对路径：直接收的话 /__extdoc?path=/etc/…
# 就是一条任读本机文件的口子。登记之后前端手里只有一个不带含义的 id，
# 能读到什么由这张表说了算，路径校验只在登记那一次做。
# 表只在内存里，重启即清——重开一次 AM·Note，昨天拖进来的东西自动失效。

EXT_MAX = 20_000_000                  # 单份 20 MB
EXT_KEEP = 50                         # 内存表上限，超了从最旧的挤掉
EXT_ASSET_MAX = 32_000_000            # 单张图 32 MB，防一次读爆内存
EXT_IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
               ".svg": "image/svg+xml", ".avif": "image/avif",
               ".heic": "image/heic", ".tif": "image/tiff", ".tiff": "image/tiff"}

_ext_docs = OrderedDict()             # id → {路径, 名称, 登记}


def ext_open(req: dict):
    """登记一份库外 md。库内的不收——那些走正常流程，进索引、能编辑。"""
    p = (req.get("路径") or "").strip()
    if not p or "\x00" in p or not os.path.isabs(p):
        return {"ok": False, "错误": "要一个绝对路径"}
    full = os.path.realpath(os.path.expanduser(p))
    if not full.lower().endswith(".md"):
        return {"ok": False, "错误": "只能打开 md"}
    if not os.path.isfile(full):
        return {"ok": False, "错误": "这份文件不在了"}
    if full == REAL_ROOT or full.startswith(REAL_ROOT + os.sep):
        return {"ok": False, "错误": "这份在库里，按库内文档打开"}
    try:
        size = os.path.getsize(full)
    except OSError as e:
        return {"ok": False, "错误": f"读不了：{e}"}
    if size > EXT_MAX:
        return {"ok": False, "错误": f"这份 {size // 1048576} MB，上限 {EXT_MAX // 1048576} MB"}
    with _lock:
        for k, v in _ext_docs.items():           # 同一份拖两次给同一个 id
            if v["路径"] == full:
                _ext_docs.move_to_end(k)
                return {"ok": True, "id": k, "名称": v["名称"], "字节": size}
        eid = secrets.token_hex(8)
        _ext_docs[eid] = {"路径": full, "名称": os.path.basename(full),
                          "登记": time.time()}
        while len(_ext_docs) > EXT_KEEP:
            _ext_docs.popitem(last=False)
    return {"ok": True, "id": eid, "名称": os.path.basename(full), "字节": size}


def ext_doc(query):
    """按 id 读那份库外 md 的原文。只读。"""
    eid = (query.get("id") or [""])[0]
    ent = _ext_docs.get(eid)
    if not ent:
        return {"ok": False, "错误": "这份没登记过，重新拖一次"}
    try:
        with open(ent["路径"], encoding="utf-8", errors="replace") as f:
            text = f.read(EXT_MAX)
        st = os.stat(ent["路径"])
    except OSError as e:
        return {"ok": False, "错误": f"读不了：{e}"}
    return {"ok": True, "id": eid, "名称": ent["名称"], "路径": ent["路径"],
            "正文": text, "字节": st.st_size,
            "改于": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")}


def ext_asset(query):
    """那份库外 md 同目录下的一张图。返回 (字节, content-type) 或 (None, 错误)。

    realpath 之后必须还在那份 md 所在的目录里。`../../.ssh/id_rsa` 这种
    以及指到目录外的软链接，都是在这一步挡下来的。
    """
    eid = (query.get("id") or [""])[0]
    rel = (query.get("rel") or [""])[0]
    ent = _ext_docs.get(eid)
    if not ent:
        return None, "这份没登记过，重新拖一次"
    if not rel or "\x00" in rel or os.path.isabs(rel):
        return None, "路径不合法"
    base = os.path.realpath(os.path.dirname(ent["路径"]))
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep):
        return None, "这张图不在文档旁边"
    ctype = EXT_IMG_EXT.get(os.path.splitext(full)[1].lower())
    if not ctype:
        return None, "只认图片"
    if not os.path.isfile(full):
        return None, "这张图不在了"
    try:
        if os.path.getsize(full) > EXT_ASSET_MAX:
            return None, "这张图太大了"
        with open(full, "rb") as f:
            return f.read(), ctype
    except OSError as e:
        return None, f"读不了：{e}"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, *a):
        pass

    def _body(self, b: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self._cache_sent = True
        self.end_headers()
        if not self._head_only:
            self.wfile.write(b)

    def _json(self, payload: str):
        self._body(payload.encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: str, ctype: str):
        try:
            with open(path, "rb") as f:
                b = f.read()
        except OSError:
            self.send_error(404, "not found")
            return
        self._body(b, ctype)

    def _portal_page(self):
        """出门户页，顺手把 token 注进去。

        模板里放的是字面 __AMN_TOKEN__，前端从 <meta name="amn-token"> 读。
        读到的还是占位串就说明这是旧版服务（模板新、服务旧），前端自己降级。
        """
        try:
            with open(TEMPLATE_HTML, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            self.send_error(404, "not found")
            return
        self._body(html.replace(TOKEN_PLACEHOLDER, TOKEN).encode("utf-8"),
                   "text/html; charset=utf-8")

    def _deny(self, code: int, msg: str):
        """拒掉一个请求。给 JSON 不给 html 错误页——前端一律 res.json() 读结果，
        html 错误页会让它在解析那一步炸掉，看不出是被门禁挡的。

        连接一律断开：POST 被拒时请求体还没读完，keep-alive 复用会把下一个请求
        的报文头读成上一个的正文。
        """
        b = json.dumps({"ok": False, "错误": msg}, ensure_ascii=False).encode("utf-8")
        self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self._cache_sent = True
        self.end_headers()
        if not self._head_only:
            self.wfile.write(b)

    def _gate(self) -> bool:
        """Host / Origin 两道，每个请求都过。过不了直接 403 并返回 False。"""
        port = _state["port"]
        hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
        if (self.headers.get("Host") or "").strip() not in hosts:
            self._deny(403, "Host 不对")
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin not in tuple("http://" + h for h in hosts):
            self._deny(403, "跨站请求不收")
            return False
        return True

    def _token(self) -> bool:
        if token_ok(self.headers.get("X-AMN-Token") or ""):
            return True
        self._deny(403, "口令不对。退出 AM·Note 再打开一次。")
        return False

    def _q(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _route(self) -> bool:
        """自己的接口和别名走这里。返回 True 表示已经应答，不再交给静态服务。"""
        route = self.path.split("?")[0]
        if route.startswith("/__pulse"):
            self._json(json.dumps(cached_fingerprint(), ensure_ascii=False))
        elif route.startswith("/__rescan"):
            # v21 起重扫是 POST。它是个会动索引库、会跑几秒的动作，挂在 GET 上
            # 等于随便哪个页面塞个 <img src> 就能让库转一圈
            self._deny(405, "重扫要用 POST")
        elif route.startswith("/__extdoc"):
            if self._token():
                self._json(json.dumps(ext_doc(self._q()), ensure_ascii=False))
        elif route.startswith("/__extasset"):
            if self._token():
                blob, ctype = ext_asset(self._q())
                if blob is None:
                    self._deny(404, ctype)
                else:
                    self._body(blob, ctype)
        elif route.startswith("/__status"):
            self._json(json.dumps(status(), ensure_ascii=False))
        elif route.startswith("/__tree"):
            self._json(json.dumps(tree_view(), ensure_ascii=False))
        elif route.startswith("/__config"):
            self._json(json.dumps(read_config(), ensure_ascii=False))
        elif route.startswith("/__raw"):
            self._json(json.dumps(read_raw((self._q().get("path") or [""])[0]),
                                  ensure_ascii=False))
        elif route.startswith("/__search"):
            self._json(json.dumps(search_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__meta"):
            self._json(json.dumps(meta_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__changes"):
            self._json(json.dumps(changes_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__archive"):
            self._json(json.dumps(archive_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__current"):
            self._json(json.dumps(current_view(), ensure_ascii=False))
        elif route in ("/portal", "/portal/"):
            self._portal_page()
        elif route in ALIAS:
            self._file(*ALIAS[route])
        else:
            return False
        return True

    def do_GET(self):
        self._cache_sent = False
        self._head_only = False
        self._ctype = ""
        if not self._gate():
            return
        if not self._route():
            super().do_GET()

    def do_HEAD(self):
        self._cache_sent = False
        self._head_only = True
        self._ctype = ""
        if not self._gate():
            return
        if not self._route():
            super().do_HEAD()

    def do_POST(self):
        self._cache_sent = False
        self._head_only = False
        self._ctype = ""
        if not self._gate():
            return
        route = self.path.split("?")[0]
        if route not in ("/__config", "/__reveal", "/__external",
                         "/__save", "/__state", "/__rescan", "/__extopen"):
            self.send_error(404, "not found")
            return
        # **所有 POST 都要口令。** 这是写路由和触发类路由的唯一一道门
        if not self._token():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if route == "/__rescan":                 # 不吃参数，直接跑
            if 0 < n <= 1_000_000:
                self.rfile.read(n)               # body 读干净，不然 keep-alive 会错位
            self._json(json.dumps(rescan(), ensure_ascii=False))
            return
        # /__save 传的是整篇正文，上限单独给大一点
        cap = 9_000_000 if route == "/__save" else 1_000_000
        if n <= 0 or n > cap:
            self._json(json.dumps({"ok": False, "错误": "请求体为空或过大"},
                                  ensure_ascii=False))
            return
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._json(json.dumps({"ok": False, "错误": f"请求不是合法 JSON：{e}"},
                                  ensure_ascii=False))
            return
        try:
            out = {"/__config": write_config, "/__reveal": reveal,
                   "/__external": open_external, "/__extopen": ext_open,
                   "/__save": save_route, "/__state": state_set}[route](req)
        except Exception as e:                       # 界面上要看得见，不能静默 500
            out = {"ok": False, "错误": f"{type(e).__name__}: {e}"}
        # 补一轮全文同步，索引在几秒内就能跟上这次改动。
        # **记账不在这儿**：save_md / new_md 在真正落盘前就记了，见那两处注释
        if route == "/__save" and isinstance(out, dict) and out.get("ok"):
            kick_sync()
        self._json(json.dumps(out, ensure_ascii=False))

    def send_header(self, keyword, value):
        # end_headers 按 Content-Type 决定要不要沙箱，先记下来
        if keyword.lower() == "content-type":
            self._ctype = str(value)
        super().send_header(keyword, value)

    def _is_vault_html(self) -> bool:
        """库内 html 才套沙箱。

        iframe 已经 sandbox=""；顶层打开（地址栏改成 /某页.html）没有
        iframe 那层，必须靠响应头再挡一次——脚本一旦跑起来就跟门户同源，
        能读到页面里的 token。/portal 自己要跑 JS，不套。
        """
        route = self.path.split("?")[0]
        if route in ("/portal", "/portal/"):
            return False
        return (getattr(self, "_ctype", "") or "").lower().startswith("text/html")

    def end_headers(self):
        if not getattr(self, "_cache_sent", False) \
                and self.path.endswith((".md", ".html", ".htm", ".json")):
            self.send_header("Cache-Control", "no-store")
        if self._is_vault_html():
            self.send_header("Content-Security-Policy", "sandbox")
            self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def serve(port_from=None, port_to=None):
    if port_from is None or port_to is None:
        port_from, port_to = cfg()["端口范围"]
    for port in range(port_from, port_to + 1):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            continue
        _state["port"] = port
        return httpd, port
    raise SystemExit("没有可用端口")


if __name__ == "__main__":
    # 口令文件默认 ~/Library/Application Support/AMNote/portal.token，
    # 可以用 --token-file 或环境变量 AMN_TOKEN_FILE 挪走。
    root, argv = fulltext.take_root_arg(sys.argv[1:])
    if "--token-file" in argv:
        i = argv.index("--token-file")
        if i + 1 < len(argv):
            TOKEN_FILE = argv[i + 1]
    fulltext.configure(root)
    _bind_vault()
    token_dir = os.path.dirname(os.path.abspath(TOKEN_FILE))
    if token_dir:
        os.makedirs(token_dir, exist_ok=True)
    httpd, port = serve()
    write_token_file()
    # 开机先补一轮全文同步：把「客户端关着的那段时间里库里动了什么」
    # 记进流水、该留档的留档。后台跑，不挡窗口出内容。
    kick_sync()
    print(port, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # 正常退出把口令文件收走。留着不至于出事（下一趟会覆盖），
        # 但留一个连不上任何服务的口令，排查时容易看岔
        try:
            os.remove(TOKEN_FILE)
        except OSError:
            pass
