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
       改库内文件内容的路由仍然只有 /__save 一条。

v22（删除入口）新增两条搬文件的路由，见下方「写库内文件的路由」那一段：
    /__trash / /__untrash —— 移进废纸篓 ／ 撤销。**只搬位置，不改内容。**

v24（5.6，个人资料 ＋ Agent 协作）加了三摊，都在下面各自的段落里：
    ① 个人资料 —— GET/POST /__profile、GET /__avatar。落在 --support-dir
       指的目录里（默认 ~/Library/Application Support/AMNote），**不进库**。
    ② 给 Agent 用的读接口 —— /__map（库地图）、/__outline（一份的大纲）、
       /__links（出链反链）、/__recent（最近改了什么），外加 /__search 的
       筛选参数和 /__raw 的 section= / lines=。全是只读、免口令的 GET。
    ③ 署名与接入 —— 所有 POST 认 X-AMN-Agent 头，流水上记「来源: Agent」
       ＋「代理: 名字」；/__agent_setup 一条路装命令行工具、Claude Code 的
       skill、库根的 AGENTS.md 和导出的库地图。

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
    /__trash     POST，{路径}，把一份 md/html 移进 ~/.Trash/。见下。      ← v22
    /__untrash   POST，{路径}，把刚才那份从废纸篓挪回原位（前端的「撤销」）。← v22

    /portal      → 同目录 template.html。
                 模板里的 /*__DATA__*/null/*__DATA__*/ 占位符原样留着不注入，
                 前端读到 null 就全部走 /__tree。改前端不用再重扫，刷新即新版。
    /icon-192.png /icon-512.png → 脚本同目录下的同名文件

只绑 127.0.0.1，不对外。

**写库内文件的路由一共三条，分两类：**
    · **改内容的只有 /__save 一条**——新建、贴图、覆写都挂在它下面，
      放开到全库 md。别的路由一个字节的正文都不写。
    · **只搬位置不改内容的是 /__trash 和 /__untrash**（v22）：把一份 md/html
      挪进当前用户的 ~/.Trash/，或者把刚挪走的那份挪回原位。文件原样搬走，
      内容不动，废纸篓里还捞得回来。撤销表只在内存里，重启即清。
      流水由随后那趟同步落，一次删除只留一行「删除／门户」（v23，见 trash()）。

三条共用的保险是口令门禁——没有 token 一条都打不进。/__save 那三道老保险照旧：
    ① 写前把旧内容留进 .amnote/backups/，每份留最近 10 版（v21 起按时间节流）；
    ② 先写临时文件再 os.replace，中途断电不会留半截文件；
    ③ 打开编辑之后这份在别处被改过的，先拦一次，要前台再确认。
挡住的位置见 _edit_ok（写）和 _trash_ok（搬）：备份目录、缓存目录、隐藏目录
一律不给动，两处判据逐条对应，只差认哪些后缀。

v20 撤掉的路由（代码已删）：
    /__sheet、/__untagged、POST /__tag、/__backlinks、/__deadlinks、
    /__inbox、POST /__inbox_read、/manifest.json、POST /__open。
"""

import array
import base64
import errno
import hmac
import json
import os
import re
import secrets
import shlex
import signal
import shutil
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
from collections import OrderedDict, deque
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 库根、配置、跳过／噪声规则、全文索引、变更流水、留档全在 fulltext 里。
# v20 起它是唯一的数据层——scan_tags / prep_batches / apply_tags / sheet_read
# 四个模块整层退役，收录口径不再有第二份。
import fulltext
# 服务端自己生成的那几十句话（错误、保存结果、config.json 的校验意见）的
# 三语词典。界面文字在网页那边翻，不走这里。每个请求开头 set_lang 一次。
from portal_i18n import LANGS, T, gloss, set_lang

TEMPLATE_HTML = os.path.join(HERE, "template.html")
# 静态服务里「这条路不给发」的落点。translate_path 挡下来的请求返回它，
# 基类打不开就自然回 404——不用在那儿另写一条应答。
NOWHERE = os.path.join(HERE, "__amn_no_such_file__")

# /portal 不在这张表里：v21 起出页要替换 token，走 Handler._portal_page
ALIAS = {
    "/icon-192.png": (os.path.join(HERE, "icon-192.png"), "image/png"),
    "/icon-512.png": (os.path.join(HERE, "icon-512.png"), "image/png"),
    # 界面词典：键是简体中文源串，页面按当前语言查表；文件在 src/locales/
    "/__i18n/en.js": (os.path.join(HERE, "locales", "en.js"), "application/javascript; charset=utf-8"),
    "/__i18n/zh-HK.js": (os.path.join(HERE, "locales", "zh-HK.js"), "application/javascript; charset=utf-8"),
}


# ── 笔记本：一个进程挂几个文件夹（5.7）──────────────────────
#
# 一个笔记本＝用户添加的一个文件夹。合起来仍叫笔记库。列表由服务端持有
# （notebooks.json 在 support dir 里），页面、CLI、壳都从服务端读。
#
# **模式 ＝ 列表长度。** 只有一本时对外路径不带前缀、界面不显示笔记本条，
# 跟 5.6 逐字节一样；两本起所有对外路径变成「笔记本名/库内相对路径」。
# 名字就是前缀（P7：人和 Agent 都看得懂），内部另有一个 8 位 id，
# **永不出现在路径里**，只在 notebooks.json 和 /__notebooks 的动作参数里用。
#
# HTTP 层拆一次就够：resolve(对外路径) → (笔记本, rel) 并把这条线程绑到那一本，
# 底下的业务函数只见 rel 和「当前笔记本」，_view_full / _edit_ok / _trash_ok
# 三处判据一个字没改。发出去的路径统一走 out(笔记本, rel)。

# 八色盘的键名（值在设计稿 tokens.css 里）。加入时分配「用得最少、其次靠前」
# 的一色；八本以上允许重复——颜色是提示，名字才是身份。
NB_COLORS = ("blue", "teal", "green", "gold", "orange", "rose", "plum", "slate")
NB_NAME_MAX = 40                      # 名字硬上限（软上限 8 个汉字由页面提示）
NB_VERSION = 1                        # notebooks.json 的版本号

# --notebooks-file 给了才落盘。没给就是**临时列表**：/__notebooks 的增删只在
# 内存里生效（响应带 持久:false）。现有开发脚本（--root ＋ --support-dir）
# 因此永远不会把测试库写进用户真正在用的那一份。
NOTEBOOKS_FILE = ""
_nb_lock = threading.RLock()
_nb_default = ""                      # 「默认」指针（新建落到哪本），空＝主笔记本


def V():
    """这一趟请求绑着的笔记本。没绑过就是主笔记本。"""
    return fulltext.V()


def nb_all():
    return fulltext.vaults()


def nb_main():
    """主笔记本＝列表第一条。/__status 的 库根 / 随手记目录 报的是它。"""
    return fulltext.main_vault()


def nb_multi() -> bool:
    return len(fulltext.vaults()) >= 2


def nb_mode() -> str:
    return "多" if nb_multi() else "单"


def nb_default():
    """「全部」模式下新建落到哪本。缺省是主笔记本。"""
    return fulltext.get_vault(_nb_default) or nb_main()


def bind(v):
    """把这条线程绑到某一本上（传 None ＝ 回到主笔记本）。"""
    fulltext.set_current(v.vid if v is not None else None)
    return v


def _nfc(x) -> str:
    """名字一律按 NFC 比。macOS 从文件系统拿到的文件夹名可能是 NFD，
    页面送回来的是键盘打出来的 NFC，逐字节比会认不出是同一个名字。"""
    return unicodedata.normalize("NFC", str(x if x is not None else ""))


def _fold(x) -> str:
    """名字比较用：NFC ＋ casefold（大小写不敏感唯一）。"""
    return _nfc(x).casefold()


def nb_by_name(name):
    """按名字找一本（NFC ＋ 大小写不敏感）。找不到返回 None。"""
    key = _fold(name)
    if not key:
        return None
    for v in fulltext.vaults():
        if _fold(v.name) == key:
            return v
    return None


def nb_arg(name, dflt=None):
    """带外的 `nb=名字` → (笔记本, 错误)。给 `/__config`、`/__agent_setup`、
    `/__archive` 这几条「不在路径里，另给一个名字」的路由用。

    空的用缺省本（不给就是主笔记本）；**名字写错了不回落**——静默落到主笔记本
    的话，「给读书笔记那本改配置」会不声不响改在工作那本上，界面上还报成功。
    路径里的第一段走 `resolve()`，那边同样不回落，两条路一个口径。
    """
    raw = _nfc(name if name is not None else "").strip()
    if not raw:
        v = dflt or nb_main()
        return v, ("" if v is not None else T("还没有添加任何笔记本"))
    v = nb_by_name(raw)
    if v is None:
        return None, T("没有叫「{n}」这个名字的笔记本", n=raw)
    return v, ""


def out(v, rel: str) -> str:
    """发出去的路径。**只在多笔记本模式下加前缀**，单本时一个字不动。"""
    if not rel or v is None or not nb_multi():
        return rel
    return v.name + "/" + rel


def out_row(v, row, key="路径"):
    """一条带路径的记录：拷一份、路径加前缀、多本时补一个「本」。

    拷贝是必须的——树和流水那几张表是缓存出来的，就地改会把前缀写进缓存，
    下一次再加一遍。
    """
    r = dict(row)
    if key in r:
        r[key] = out(v, r[key])
    if nb_multi():
        r["本"] = v.name
    return r


def resolve(path):
    """对外路径 → (笔记本, 库内相对路径, 错误)。**顺手把这条线程绑过去。**

    多本：第一段必须是某个笔记本的名字，否则 `no_notebook`。**不回落到默认本**
    ——「默认本里恰好有个文件夹跟另一本同名」那种歧义比一条错误提示贵得多。
    单本：整串就是 rel，一个字不处理（零变化，见契约 §6.3）。
    """
    p = str(path if path is not None else "")
    if not nb_multi():
        v = nb_main()
        bind(v)
        if v is None:
            return None, "", T("还没有添加任何笔记本")
        if not v.online:
            return None, "", T("笔记本「{n}」现在找不到，它的文件夹可能被挪走了", n=v.name)
        return v, p, ""
    seg, _, rest = p.partition("/")
    v = nb_by_name(seg)
    if v is None:
        return None, "", T("路径要从笔记本的名字开始，比如「{n}/…」",
                           n=(nb_main().name if nb_main() else ""))
    if not v.online:
        return None, "", T("笔记本「{n}」现在找不到，它的文件夹可能被挪走了", n=v.name)
    bind(v)
    return v, rest, ""


def resolve_dir(sub: str):
    """`dir=` 参数（可能带笔记本前缀）→ (笔记本 或 None, 目录)。

    多本时第一段是笔记本名就限定到那一本（`dir=工作/会议` 隐含 `nb=工作`）；
    不是的话当成「全体里的这个目录」，扇出时各本各筛各的。
    """
    sub = (sub or "").strip().strip("/")
    if not sub or not nb_multi():
        return None, sub
    seg, _, rest = sub.partition("/")
    v = nb_by_name(seg)
    if v is None:
        return None, sub
    return v, rest


def nb_pick(query, key="nb"):
    """`nb=名字`（逗号可多）→ 要哪几本。不给＝全体。名字不认识的忽略。"""
    raw = (query.get(key) or [""])[0].replace("，", ",")
    want = [t.strip() for t in raw.split(",") if t.strip()]
    if not want:
        return nb_online()
    got = []
    for name in want:
        v = nb_by_name(name)
        if v is not None and v.online and v not in got:
            got.append(v)
    return got


def nb_online():
    """在线的那些。离线本不参与扇出——它的索引可能还在，但库不在了。"""
    return [v for v in fulltext.vaults() if v.online]


def cfg(v=None):
    """每次现读，改完 config.json 不用重启服务。"""
    v = v or V()
    c, _ = fulltext.load_config(v.config_path)
    return c


def note_dir(v=None):
    """随手记相对库根的目录。从配置读，空了回退默认值。"""
    d = cfg(v).get("随手记目录")
    if isinstance(d, str) and d.strip():
        return d.strip()
    return "随手记"


def _bad(msg):
    """一条出错的响应。

    `msg` 是 `T()` 的结果：既是那句给人看的话，又挂着一个稳定的 ASCII 短码。
    有短码就顺手写进 `"代码"`——页面按短码判断分支（同名占用 / 不在了 / 太大 /
    要确认…），不再靠正则匹配中文文案，换个界面语言不会认不出来。
    字段只增不改：`"错误"` 还是原来那一句。
    """
    out = {"ok": False, "错误": str(msg)}
    code = getattr(msg, "code", "")
    if code:
        out["代码"] = code
    return out


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


# ── 两个目录：本机的资料目录、写用户级文件时的家目录 ──────────
#
# **个人资料不进库。** 名字、问候开关、头像放在 macOS 给每个 app 的那个位置
# （~/Library/Application Support/AMNote），换一个笔记库不用重设，库跟着网盘
# 同步也不会把头像带到别人机器上。`--support-dir` / `AMNOTE_SUPPORT_DIR`
# 能把它整个挪走——测试时必须挪走，不然会写进用户真正在用的那一份。
# 壳启动服务时传的就是默认值。
#
# AMNOTE_HOME 只给 /__agent_setup 用：装命令行工具和 Claude Code 的 skill 要往
# ~/.local/bin 和 ~/.claude/skills 里写，测试时把「家」指到 scratch 目录。

SUPPORT_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("AMNOTE_SUPPORT_DIR")
    or "~/Library/Application Support/AMNote"))
HOME_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("AMNOTE_HOME") or "~"))

# 这一趟请求是谁发的（X-AMN-Agent 头）。每条连接一个线程，所以挂 threading.local；
# 没有这个头就是空串＝用户自己在门户里点的。
_req = threading.local()

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
NAME_MAX = 40


def clean_name(s, limit=NAME_MAX):
    """人给的名字（昵称、Agent 署名）洗一遍：去控制字符、去两头空白、截断。

    控制字符要去掉——它们会原样落进 profile.json 和 changes.jsonl，
    终端里打出来能改光标位置、清屏，甚至骗过一行日志。
    """
    return _CTRL_RE.sub("", str(s if s is not None else "")).strip()[:limit].strip()


def header_name(raw):
    """HTTP 头里那个名字。**先把 latin-1 还原成 UTF-8 再截断。**

    http.client 按 latin-1 解请求头（RFC 7230 就是这么规定的），发送方要送
    「露露的助手」只能把 UTF-8 的字节原样塞进去；这边拿到的是一串
    `Ã¦Â¼Â...`，直接洗一遍就落进流水里，用户看到的是一行乱码。
    还原不了（本来就是 ASCII、或者对方发的不是 UTF-8）就用原样那份。

    **截断排在解码之后**：先截 40 会把一个多字节字符腰斩，解码整串就废了。
    """
    s = str(raw if raw is not None else "")
    try:
        s = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return clean_name(s)


def cur_agent() -> str:
    return getattr(_req, "agent", "") or ""


_lock = threading.Lock()
_cache = {}                           # {vid: {"fp":…, "at":…, "耗时":…}}
_state = {"port": 0, "started": time.time()}


def fingerprint(v=None):
    """一本的指纹：md/html ＋ 附件的文件数和最新 mtime。任一变了就说明有动静。
    附件也算，不然新拖进来一份 pdf 要等到手动重扫才进索引。"""
    v = v or V()
    n = 0
    newest = 0.0
    c = cfg(v)
    skip = tuple(c["跳过目录关键词"]) + tuple(c["噪声目录"])
    exts = (".md", ".html", ".htm") + tuple(fulltext.ATT_EXT)
    for dp, dn, fns in os.walk(v.root):
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


def vault_fingerprint(v):
    """一本的指纹，**自适应缓存**：上一趟 walk 花了多久，就多留久一点
    （`max(2, 8×耗时)`）。一本三秒的大库乘上笔记本数，再按每 3 秒问一次的
    频率去 walk，机器会一直有一颗核在转。"""
    now = time.time()
    with _lock:
        c = _cache.get(v.vid)
        if c and now - c["at"] <= max(2.0, 8 * c["耗时"]):
            return c["fp"]
    t0 = time.time()
    with fulltext.use(v):
        fp = fingerprint(v)
    with _lock:
        _cache[v.vid] = {"fp": fp, "at": time.time(), "耗时": time.time() - t0}
    return fp


def cached_fingerprint():
    """/__pulse 的响应。**形状一个字都不能变**（前端把整串当指纹比对）：
    多本时文件数相加、最新改动取最大，离线的不算。

    离线本的复活也搭在这条路上探（每 3 秒一次，比每个请求都 isdir 便宜）。
    """
    recheck_offline()
    n, newest = 0, 0.0
    for v in nb_all():
        if not v.online:
            continue
        fp = vault_fingerprint(v)
        n += fp["文件数"]
        newest = max(newest, fp["最新改动"])
    return {"文件数": n, "最新改动": round(newest, 1)}


def recheck_offline():
    """外置盘拔了 ／ iCloud 还没就绪的那些，探一眼。

    回来了就挂上并补一轮扫描；本来在线的文件夹被挪走了就标成离线——
    指向它的请求从此回 `offline`，而不是在库根之外的地方乱找。
    """
    for v in nb_all():
        alive = os.path.isdir(v.root)
        if v.online != alive:
            v.online = alive
            if alive:
                fulltext._ensure_dirs(v)
                kick_sync(v)


# ── 标题与预览 ────────────────────────────────────
#
# v20 起标题不再从标签块里读（标签层整层退役，库里 900 多份 md 的标签块一个字
# 不动，只是门户不再读它）。推导顺序：md 取正文第一个 `# ` 标题；html 取抽取
# 正文的第一行（_extract_html 把 <title> 放在最前）；都取不到再退回文件名主干。

# 标签块、`# 标题`、文件名主干、html 的 <title>：这四样搬进 fulltext 了
# （命令行离线画地图要用同一份口径，见 fulltext 的「标题、切片、库地图」那一段）。
# 这里留四个别名，本文件里的用法一个字不改。
TAG_HEAD_RE = fulltext.TAG_HEAD_RE
first_heading = fulltext.first_heading
clean_title = fulltext.clean_title
html_title = fulltext.html_title


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


def title_of(rel: str, head: str, kind: str, generic) -> str:
    if kind == "md":
        t = first_heading(head)
    elif kind == "html":
        t = html_title(head)
    else:
        t = ""
    return disambiguate(t or clean_title(os.path.basename(rel)), rel, generic)


list_preview = fulltext.list_preview   # 命令行离线列树用的也是它，见 fulltext


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
        return None, T("路径不合法")
    v = V()
    full = os.path.realpath(os.path.join(v.root, rel))
    if not (full == v.real_root or full.startswith(v.real_root + os.sep)):
        return None, T("路径越出库根")
    if not os.path.isfile(full):
        return None, T("文件不在了")
    if not full.lower().endswith((".md", ".html", ".htm") + tuple(fulltext.ATT_EXT)):
        return None, T("这个格式门户不认")
    return full, ""


# ── 访达 ／ 系统默认程序 ／ 剪贴板 ─────────────────

def reveal(req: dict):
    """在访达里选中这份文件。只是打开一个窗口，不动文件。

    路径为空＝主笔记本的根（设置页「在访达中显示」那颗按钮）；多本时一个
    笔记本的名字（「工作」或「工作/」）＝那一本的根。_view_full 只认库内的
    文件，库根是目录、相对路径又是空串，两条它都不放行，所以这两种在这里
    单独处理：直接 open 那一本的根。越界、格式、存在性那几道对文件的检查
    一个都没松——它们压根不经过那些，走的是那一本自己的 real_root。"""
    raw = (req.get("路径") or "").strip()
    # 空串＝主笔记本的根；多本时「工作」「工作/」＝那一本的根（设置里每行那颗
    # 「在访达里显示」按的就是这条）
    v = nb_main() if not raw else nb_by_name(raw.rstrip("/")) if nb_multi() else None
    if v is not None:
        bind(v)
        if not os.path.isdir(v.real_root):
            return _bad(T("库根不在了"))
        try:
            subprocess.run(["open", v.real_root], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return _bad(T("打不开访达：{e}", e=e))
        return {"ok": True, "路径": v.real_root}
    v, rel, err = resolve(raw)
    if err:
        return _bad(err)
    full, err = _view_full(rel)
    if err:
        return _bad(err)
    try:
        subprocess.run(["open", "-R", full], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return _bad(T("打不开访达：{e}", e=e))
    return {"ok": True, "路径": full}


def open_external(req: dict):
    """交给系统默认程序打开。pdf / xlsx 走这条；html 默认在标签里读，
    「在浏览器里打开」才落到这里。

    走「系统默认」而不是写死 Chrome：这台机器上 https 和 public.html 两个默认
    处理程序都是 com.google.chrome（20260825 实测），效果一样，以后换浏览器
    也不用改代码。
    """
    v, rel, err = resolve((req.get("路径") or "").strip())
    if err:
        return _bad(err)
    full, err = _view_full(rel)
    if err:
        return _bad(err)
    try:
        subprocess.run(["open", full], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return _bad(T("打不开：{e}", e=e))
    return {"ok": True, "路径": full}


# 「第几行」、按标题切一节、按行号切一段：都在 fulltext 里（命令行离线读
# 走的是同一份，见 fulltext.text_slice）。这里只留别名和一张错误码 → 话的表。
_lines_of = fulltext.lines_of

SLICE_ERR = {"bad_lines": "行号要写成 A-B，都从 1 数起",
             "out_of_range": "行号超出这份的范围",
             "no_section": "找不到这个小节"}


def read_raw(rel: str, section: str = "", lines: str = ""):
    """编辑器要的是源码，原样给。渲染那条路走静态服务，不走这里。

    5.6 多两个参数，都是给 Agent 用的——一份两千行的 md，为了看其中一节
    把整份灌进上下文是浪费：
        section=<标题文本>   只要那一节（见 fulltext.md_section）
        lines=A-B           只要那几行，1-based 闭区间
    两个都给时 lines 说了算。门户自己只传 path，走的还是「整份原样给」那条。
    「字节」照旧是整份文件的大小，不是这一段的——前端拿它认「这份能不能编辑」。
    """
    v, rel, err = resolve(rel)
    if err:
        return _bad(err)
    full, err = _view_full(rel)
    if err:
        return _bad(err)
    if not full.endswith(".md"):
        return _bad(T("只有 md 能在门户里读源码"))
    try:
        with open(full, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return _bad(T("读不了：{e}", e=e))
    cut, err = fulltext.text_slice(text, section=section, lines=lines)
    if err:
        return _bad(T(SLICE_ERR[err]))
    return {"ok": True, "路径": out(v, rel), "正文": cut["正文"],
            "字节": os.path.getsize(full),
            "行起": cut["行起"], "行止": cut["行止"], "行数": cut["行数"],
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
        return None, T("路径不合法")
    if not rel.endswith(".md"):
        return None, T("只有 md 能在门户里改")
    for seg in rel.split("/"):
        if not seg or _seg_blocked(seg):
            return None, T("这个位置不给写")
    v = V()
    full = os.path.realpath(os.path.join(v.root, rel))
    if not full.startswith(v.real_root + os.sep):
        return None, T("路径越出库根")
    for seg in os.path.relpath(full, v.real_root).split(os.sep):
        if _seg_blocked(seg):
            return None, T("这个位置不给写")
    return full, ""


def _edit_full(rel: str, must_exist: bool):
    full, err = _edit_ok(rel)
    if err:
        return None, err
    if must_exist and not os.path.isfile(full):
        return None, T("文件不在了")
    if not must_exist and os.path.exists(full):
        return None, T("同名文件已经有了")
    return full, ""


def _last_backup(flat: str):
    """这份文件最近一次留档的 (文件名, 写入时间)。没有留档返回 (None, 0)。

    文件名里的时间戳是 %Y%m%d-%H%M%S-%f，字典序就是时间序，排完取最后一个。
    """
    bak_dir = V().backup_dir
    try:
        olds = sorted(fn for fn in os.listdir(bak_dir)
                      if fn.startswith(flat + "__") and fn.endswith(".bak"))
    except OSError:
        return None, 0.0
    if not olds:
        return None, 0.0
    try:
        return olds[-1], os.path.getmtime(os.path.join(bak_dir, olds[-1]))
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
    bak_dir = V().backup_dir
    os.makedirs(bak_dir, exist_ok=True)
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
        with open(os.path.join(bak_dir, name), "w", encoding="utf-8") as f:
            f.write(old)
    except OSError:
        return ""                                # 备份写不了不该拦住正常保存
    olds = sorted(fn for fn in os.listdir(bak_dir)
                  if fn.startswith(flat + "__") and fn.endswith(".bak"))
    for fn in olds[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(bak_dir, fn))
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
        return _bad(T("只能新建 md"))
    if not isinstance(body, str) or not body.strip():
        return _bad(T("正文不能是空的"))
    if len(body.encode("utf-8")) > 1_000_000:
        return _bad(T("新建时正文别超过 1 MB"))
    full, err = _edit_full(rel, must_exist=False)
    if err:
        return _bad(err)
    # **门户不替用户在库里造目录。** 新建放开到全库之后，随手写个名字就能
    # 长出一层新文件夹。只有随手记目录例外——第一次用时得让它自己长出来
    parent = os.path.dirname(full)
    if not os.path.isdir(parent):
        if not rel.startswith(note_dir() + "/"):
            return _bad(T("这个文件夹还不存在，先在访达里建好"))
    try:
        os.makedirs(parent, exist_ok=True)
        note_portal_write(rel)                   # 同 save_md：记账赶在文件出现之前
        # "x" 模式：文件已存在就抛。跟上面的检查重了一道，防的是连点两下撞车
        with open(full, "x", encoding="utf-8") as f:
            f.write(body)
    except FileExistsError:
        return _bad(T("同名文件刚被建走了，换个标题"))
    except OSError as e:
        return _bad(T("写不进去：{e}", e=e))
    # 「改于」跟 save_md 一个格式：命令行建完这一份就把它记进读表，
    # 紧接着的一次 save 才有「基于」可填，不用先白跑一趟 /__meta
    return {"ok": True, "路径": rel, "字节": len(body.encode("utf-8")),
            "改于": _mtime_str(full)}


def save_img(req: dict):
    """把粘贴进来的图片写进那份 md 旁边的 _图/。v21 起随手记之外的 md 也能贴。

    文件名一律服务端按时间戳生成，不收前端给的名——收了就等于把「往库里
    写任意文件名」这个能力交出去了。
    """
    rel = (req.get("路径") or "").strip()          # 那份 md 的相对路径
    raw64 = (req.get("图片") or {}).get("数据") or ""
    md_full, err = _edit_full(rel, must_exist=True)
    if err:
        return _bad(err)
    if len(raw64) > IMG_MAX * 4 // 3 + 1024:       # base64 撑大 4/3，先卡一道免得白解
        return _bad(T("这张超过 {m} MB 了", m=IMG_MAX // 1024 // 1024))
    try:
        blob = base64.b64decode(raw64, validate=True)
    except Exception:
        return _bad(T("图片数据不对"))
    if not blob:
        return _bad(T("图片是空的"))
    if len(blob) > IMG_MAX:
        return _bad(T("这张 {n} MB，上限 {m} MB",
                      n=len(blob) // 1024 // 1024, m=IMG_MAX // 1024 // 1024))
    ext = _sniff_img(blob)
    if not ext:
        return _bad(T("只收 png / jpg / gif / webp"))

    d = os.path.join(os.path.dirname(md_full), IMG_SUB)
    name = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + "." + ext
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "xb") as f:
            f.write(blob)
    except OSError as e:
        return _bad(T("图片写不进去：{e}", e=e))
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
        return rel, T("只能改文件名")
    dest, err = _edit_full(new_rel, must_exist=False)
    if err:
        return rel, err
    src, err = _edit_full(rel, must_exist=True)
    if err:
        return rel, err
    try:
        note_portal_write(new_rel)
        # 旧名字在下一趟同步里是一条 removed。不记这一笔就落成「删除／外部」——
        # 随手记写出 H1 自动改名，流水上会冒出一行「不是我干的」。
        # 改名不动 mtime，所以走搬动表（note_portal_write 那张按 mtime 认领，对不上）。
        fulltext.note_portal_move(rel, agent=cur_agent())
        os.rename(src, dest)
    except OSError as e:
        fulltext.take_portal_move(rel)            # 没改成，把刚记的那笔收回来
        return rel, T("改名失败：{e}", e=e)
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
        return _bad(err)
    if not isinstance(body, str):
        return _bad(T("正文得是文本"))
    if len(body.encode("utf-8")) > 8_000_000:
        return _bad(T("这份太大了（超过 8 MB），别在门户里改"))

    try:
        with open(full, encoding="utf-8") as f:
            old = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return _bad(T("读不了原文：{e}", e=e))

    if old == body:
        return {"ok": True, "结果": str(T("没有改动")), "路径": rel,
                "字节": {"写前": len(old.encode()), "写后": len(old.encode())},
                "改于": _mtime_str(full)}

    # 编辑期间这份被别处改过（另一个标签、编辑器、外包脚本）→ 先拦一次，别默默盖掉
    based = (req.get("基于") or "").strip()
    now = _mtime_str(full)
    clash = bool(based and based != now)
    if clash and not force:
        out = _bad(T("你打开编辑之后，这份在别处被改过（{now}）。继续保存会盖掉那次改动。",
                     now=now))
        out["需确认"] = True
        return out

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
        return _bad(T("写失败：{e}", e=e))

    new_rel = (req.get("新路径") or "").strip()
    if new_rel:
        rel, _ = _rename_md(rel, new_rel)
        full, err2 = _edit_ok(rel)
        if err2 or not full:
            full = os.path.realpath(os.path.join(V().root, rel))

    return {"ok": True, "结果": str(T("已保存")), "路径": rel,
            "字节": {"写前": len(old.encode()), "写后": len(body.encode())},
            "备份": os.path.relpath(V().backup_dir, V().root).replace(os.sep, "/"),
            "留档": bak,                          # 空串＝这一次按节流跳过了
            "改于": _mtime_str(full)}             # 落盘后的 mtime，见函数上方那段


def save_route(req: dict):
    """/__save 一条路上的三件事，按请求里的字段分派。

    写入范围在这里统一卡一道：三条分支的「路径」都得先过 _edit_ok。
    下游三个函数各自还会再校验一次——这一道是给「一眼看清写入范围」用的，
    别为了不重复就把它删了。
    """
    v, rel, err = resolve((req.get("路径") or "").strip())
    if err:
        return _bad(err)
    _, err = _edit_ok(rel)
    if err:
        return _bad(err)
    # 拆完前缀再往下走：三个函数只见库内相对路径，跟单库时一模一样
    req = dict(req, 路径=rel)
    new_rel = (req.get("新路径") or "").strip()
    if new_rel and nb_multi():
        pre = v.name + "/"
        req["新路径"] = new_rel[len(pre):] if new_rel.startswith(pre) else new_rel
    if req.get("图片"):
        return save_img(req)
    r = new_md(req) if req.get("新建") else save_md(req)
    if isinstance(r, dict) and r.get("路径"):
        r["路径"] = out(v, r["路径"])             # 改名可能换了末段，按结果加前缀
    return r


# ── 删除：移进废纸篓，几秒内可撤销 ──────────────────────────────
#
# 「建错了一份随手记」是最常见的一次误操作，之前门户里没有出口，只能去访达删。
# 这里给两条路由，语义是**搬文件，不是删内容**：
#     /__trash    把这份挪进当前用户的 ~/.Trash/，原样躺着，能从访达捞回来
#     /__untrash  刚才那一下挪回原位（前端 toast 上那颗「撤销」）
# 不做「库内回收站」：库里多一个隐藏目录，同步、索引、留档、静态服务都得跟着
# 开一条特例；系统废纸篓是用户本来就认得的地方，捞回来不用教。
#
# 挡的位置跟写入那条路同一套判据（_seg_blocked：隐藏目录 / 备份 / 缓存 /
# .amnote），只把「只认 md」放宽到 md/html/htm——门户里能打开的就这三种。
# 段一样判两遍（相对路径一遍、realpath 之后按实际落点再判一遍），理由见 _edit_ok：
# 只判前者的话，一条指向库外的软链接就把「库根之内」绕过去了。

TRASH_DIR = os.path.expanduser("~/.Trash")
TRASH_EXT = (".md", ".html", ".htm")
TRASH_KEEP = 50                       # 撤销表最多记这么多份，超了挤掉最旧的
TRASH_TTL = 24 * 3600                 # 记了这么久还没撤，就不该再从这条路撤了

# (笔记本 id, 相对路径) → {废纸篓: 绝对路径, 原位: 绝对路径, 路径: 相对路径,
# 时间: 时间戳}。**键带着笔记本 id**：两本里各有一份「会议/周会.md」时，
# 只按相对路径记的话，在 A 本里删一份、在 B 本里点撤销，会把 A 本那份挪到
# B 本的位置上去。键和「路径」都是**盘上逐字的**那一份（见 _disk_name）。只在内存里，
# 重启即清：撤销是「刚点错那几秒」的事，隔了一次重启该走访达，不是这条路由。
#
# **「原位」记的是搬走那一刻的真实落点，撤销时按它挪回去，不拿相对路径现算。**
# 后缀比对是不分大小写的（跟 _view_full 一个口径，树上列得出来的就删得掉），
# 而 macOS 的盘默认不分大小写：请求写 a.MD、盘上是 a.md 也能删掉，现算一遍
# 就会把它「撤销」成 a.MD——文件是回来了，名字被改了一个字。
_trashed = OrderedDict()


def _disk_name(full: str):
    """盘上那一份文件逐字的名字；没有这份就返回 None。

    **realpath 不做大小写规范化。** 后缀比对是不分大小写的（跟 _view_full 一个
    口径，树上列得出来的就删得掉），而 macOS 的盘默认也不分大小写：请求写
    周末计划.MD、盘上是周末计划.md，realpath 照样把 .MD 原样还回来。拿它当废纸篓
    里的落点，废纸篓里就多出一个改了名的文件；撤销时又按这个名字挪回去，盘上
    那份真的被改了名。fulltext 的活表也是按这个字符串认领的，对不上就落一行
    「外部」。所以在这儿扫一遍父目录，取盘上那一条逐字的名字。"""
    d, name = os.path.split(full)
    hit = None
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.name == name:            # 逐字对上，不用校正
                    return name
                if hit is None and e.name.lower() == name.lower():
                    hit = e.name
    except OSError:
        return None
    return hit


def _fix_rel(rel: str, full: str) -> str:
    """请求里的相对路径，末段换成盘上逐字的那个名字。"""
    name = os.path.basename(full)
    head = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return (head + "/" + name) if head else name


def _trash_ok(rel: str, on_disk: bool = True):
    """能不能挪这份。返回 (绝对路径, 错误)，错误为空串即放行。

    判据和 _edit_ok 逐条对应，两处差别只有后缀：写只认 md，挪认 md/html/htm。
    on_disk=True 时顺带把末段的大小写校正成盘上那一份（见 _disk_name）；
    撤销那条路上文件已经在废纸篓里了，盘上本来就没有，那边传 False。
    """
    if not rel or rel.startswith("/") or "\x00" in rel or ".." in rel.split("/"):
        return None, T("路径不合法")
    if not rel.lower().endswith(TRASH_EXT):
        return None, T("只有笔记和网页能删")
    for seg in rel.split("/"):
        if not seg or _seg_blocked(seg):
            return None, T("这个位置不给删")
    v = V()
    full = os.path.realpath(os.path.join(v.root, rel))
    if not full.startswith(v.real_root + os.sep):
        return None, T("路径越出库根")
    for seg in os.path.relpath(full, v.real_root).split(os.sep):
        if _seg_blocked(seg):
            return None, T("这个位置不给删")
    if on_disk:
        name = _disk_name(full)
        if name is None:
            return None, T("文件不在了")
        full = os.path.join(os.path.dirname(full), name)
    return full, ""


def _move_file(src: str, dest: str):
    """搬一份文件。同盘 os.replace 就是一次原子改名；库和家目录不在一个卷上时
    它会抛 EXDEV，那种才退回 shutil.move（复制＋删原件，慢但跨得过去）。"""
    try:
        os.replace(src, dest)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.move(src, dest)


def _trash_dest(name: str) -> str:
    """废纸篓里的落点。重名就在扩展名前面缀一个 " (HH-MM-SS)"，跟访达自己
    重名时的做法一个意思——同一份文件删两回，废纸篓里要看得出哪份是哪份。"""
    dest = os.path.join(TRASH_DIR, name)
    if not os.path.lexists(dest):
        return dest
    stem, ext = os.path.splitext(name)
    stamp = datetime.now().strftime("%H-%M-%S")
    dest = os.path.join(TRASH_DIR, "%s (%s)%s" % (stem, stamp, ext))
    n = 2
    while os.path.lexists(dest):                 # 同一秒内删第三份才走得到
        dest = os.path.join(TRASH_DIR, "%s (%s-%d)%s" % (stem, stamp, n, ext))
        n += 1
    return dest


def _prune_trashed(now: float):
    """撤销表剪枝。**调用方必须已经拿着 _lock。**

    两把尺子：条数（超了挤掉最旧的）和时间。只有条数的话，一台开着不关的机器上
    删一份就永远占着一格，指着几个钟头前那条废纸篓记录——那会儿用户早从访达里
    自己处理过了，撤销该走访达而不是这条路由。
    """
    while len(_trashed) > TRASH_KEEP:
        _trashed.popitem(last=False)
    for k in list(_trashed):
        if now - (_trashed[k].get("时间") or 0) >= TRASH_TTL:
            _trashed.pop(k, None)


def _find_trashed(vid: str, rel: str):
    """按 (笔记本, 相对路径) 找那条撤销记录，返回 (键, 记录)。
    **调用方必须已经拿着 _lock。**

    表是按盘上逐字的路径记的，而前端撤销时送回来的是它当初请求用的那一份；
    大小写不敏感的盘上这两个可能差着几个字母，所以先逐字找，再不分大小写找一遍。
    笔记本那一维永远逐字比——id 是我们自己发的十六进制。
    """
    key = (vid, rel)
    rec = _trashed.get(key)
    if rec is not None:
        return key, rec
    low = rel.lower()
    for k in _trashed:
        if k[0] == vid and k[1].lower() == low:
            return k, _trashed[k]
    return key, None


def trash(req: dict):
    """把一份笔记挪进废纸篓。内容一个字节不动，只是换了个地方躺着。

    **流水不在这儿记。** 挪走之前先在 fulltext 的「门户搬动」活表里记一笔，
    随后 kick_sync 那一趟发现文件没了，认领这一笔、按「门户」记一行「删除」，
    顺带把 db 里的原文留进 backups/。自己再补一行的话，同一次删除会在流水上
    留下两行（一行门户、一行外部）——v22 就是那样，v23 合成一行。
    """
    v, rel, err = resolve((req.get("路径") or "").strip())
    if err:
        return _bad(err)
    full, err = _trash_ok(rel)
    if err:
        return _bad(err)
    if not os.path.isfile(full):
        return _bad(T("文件不在了"))
    try:
        os.makedirs(TRASH_DIR, exist_ok=True)
    except OSError as e:
        return _bad(T("打不开废纸篓：{e}", e=e))
    # 废纸篓里的名字、活表里的路径，都用盘上逐字的那一份，不用请求里的大小写
    real_rel = _fix_rel(rel, full)
    dest = _trash_dest(os.path.basename(full))
    try:
        # 记账赶在文件消失之前，同 save_md：晚一步就会被同步判成「外部删除」
        fulltext.note_portal_move(real_rel, agent=cur_agent())
        _move_file(full, dest)
    except OSError as e:
        fulltext.take_portal_move(real_rel)       # 没挪成，把刚记的那笔收回来
        return _bad(T("挪不进废纸篓：{e}", e=e))
    now = time.time()
    key = (v.vid, real_rel)
    with _lock:
        _trashed[key] = {"废纸篓": dest, "原位": full,
                         "路径": real_rel, "时间": now}
        _trashed.move_to_end(key)                # 同一份删两回，记最新那次
        _prune_trashed(now)
    return {"ok": True, "路径": out(v, real_rel), "废纸篓": dest}


def untrash(req: dict):
    """把刚挪走的那份挪回原位。前端 toast 上那颗「撤销」按的就是这条。

    三种情况不干：表里没有（重启过，或者早就撤过了）、废纸篓里那份已经不在了
    （用户自己清空了废纸篓）、原来那个位置这会儿被别的文件占着。

    流水同 trash：只记活表，让随后那趟同步落一行「新增／门户」。
    **这里不能用 note_portal_write。** 那张表按「记账时刻离 mtime 多近」认领，
    而挪回来不动 mtime——一份上周写的笔记，撤销时 mtime 还是上周，一条都对不上。
    """
    v, rel, err = resolve((req.get("路径") or "").strip())
    if err:
        return _bad(err)
    # 盘上已经没有这份了（就在废纸篓里躺着），大小写校正这一步做不了也不用做
    _, err = _trash_ok(rel, on_disk=False)
    if err:
        return _bad(err)
    with _lock:
        _prune_trashed(time.time())
        key, rec = _find_trashed(v.vid, rel)
    if not rec:
        return _bad(T("这份撤不回来了，去废纸篓里找"))
    src, full = rec["废纸篓"], rec["原位"]
    real_rel = rec.get("路径") or key
    if not os.path.isfile(src):
        with _lock:
            _trashed.pop(key, None)
        return _bad(T("废纸篓里已经没有这份了"))
    if os.path.lexists(full):
        return _bad(T("原来那个位置又有文件了，先挪开"))
    if not os.path.isdir(os.path.dirname(full)):
        return _bad(T("原来那个目录不在了"))
    try:
        # 记账赶在文件出现之前，同 save_md：晚一步就会被同步判成「外部新增」
        fulltext.note_portal_move(real_rel, agent=cur_agent())
        _move_file(src, full)
    except OSError as e:
        fulltext.take_portal_move(real_rel)       # 没挪成，把刚记的那笔收回来
        return _bad(T("挪不回去：{e}", e=e))
    with _lock:
        _trashed.pop(key, None)
    return {"ok": True, "路径": out(v, real_rel)}


# ── 配置 ──────────────────────────────────────────

# 设置面板只编辑这几项。板块名、以及老 config.json 里残留的
# 主题清单 / 目录默认主题 / 默认主题 / 索引文件（随标签体系退役），
# 写回时原样留着不动——门户不该顺手把配置文件里别的东西删了。
EDITABLE = ("跳过目录关键词", "噪声目录", "噪声文件", "通用标题",
            "端口范围", "随手记目录")


def read_config(v=None):
    """一本的配置。`nb=名字` 选哪一本，缺省是主笔记本（**端口范围只认主本**，
    多本时另外几本填了也不作数——端口是进程的事，不是笔记本的事）。"""
    v = v or V()
    path = v.config_path
    c, problems = fulltext.load_config(path)
    raw = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
    return {"配置": c, "默认值": fulltext.DEFAULTS, "可编辑": list(EDITABLE),
            "问题": problems, "文件存在": os.path.exists(path),
            "状态": status(), "笔记本": v.name,
            "说明": {k: val for k, val in raw.items() if k.startswith("_")}}


def write_config(req: dict):
    """只认 EDITABLE 那几个键，其余一律忽略（不报错——本机可能还留着旧配置）。"""
    if not isinstance(req, dict):
        return _bad(T("配置得是一个对象"))
    v, err = nb_arg(req.get("nb") or req.get("笔记本"))
    if err:
        return _bad(err)
    bind(v)
    config_path = v.config_path
    out = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                out = json.load(f)
        except (OSError, ValueError):
            out = {}
    if not isinstance(out, dict):
        out = {}

    for k in EDITABLE:
        if k not in req:
            continue
        val = req[k]                              # 别叫 v，那是上面那一本
        if not isinstance(val, type(fulltext.DEFAULTS[k])):
            return _bad(T("「{k}」类型不对", k=k, g=gloss(k)))
        out[k] = val

    pr = out.get("端口范围") or fulltext.DEFAULTS["端口范围"]
    if not (isinstance(pr, list) and len(pr) == 2
            and all(isinstance(x, int) for x in pr)
            and 1 <= pr[0] <= pr[1] <= 65535):
        return _bad(T("端口范围要填两个 1-65535 的整数，前小后大"))

    try:
        os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, indent=2)
    except OSError as e:
        return _bad(T("写失败：{e}", e=e))
    _, problems = fulltext.load_config(config_path)
    return {"ok": True, "问题": problems, "笔记本": v.name}


# ── 个人资料：名字、问候开关、头像 ─────────────────────────────
#
# 两个文件，都在 SUPPORT_DIR 里（见那一段注释）：
#   profile.json   {"名字", "问候", "头像类型", "更新于"}；文件不在＝全默认
#   avatar.img     头像的原始字节，后缀记在「头像类型」里（png/jpg/gif/webp）
#
# 读免口令、写要口令：页面上的 <img src="/__avatar?v=…"> 带不了自定义头，
# 跟库内图片、字体那两条一个道理。**一个字节都不出本机**：这里没有任何上传。

PROFILE_JSON = "profile.json"
AVATAR_FILE = "avatar.img"
AVATAR_MAX = 1_000_000                # 页面上传前会裁成 256×256 的 png，撑死几十 KB
AVATAR_CTYPE = {"png": "image/png", "jpg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}


def profile_path():
    return os.path.join(SUPPORT_DIR, PROFILE_JSON)


def avatar_path():
    return os.path.join(SUPPORT_DIR, AVATAR_FILE)


def default_name() -> str:
    """这台 Mac 的账户全名。「系统设置 → 用户与群组」里那个名字。

    pw_gecos 在 macOS 上就是全名（有些系统里是 "全名,办公室,电话,…"），
    取逗号前那一段。取不到就退回短用户名。
    """
    try:
        import pwd
        full = (pwd.getpwuid(os.getuid()).pw_gecos or "").split(",")[0].strip()
        if full:
            return full
    except Exception:
        pass
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return ""


def profile_read() -> dict:
    """profile.json 的内容。不在、读不了、不是对象一律当全默认（空 dict）。"""
    try:
        with open(profile_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def profile_view():
    """GET /__profile 的形状。POST 成功之后也回这一份。

    「头像版本」＝avatar.img 的 mtime 取整，页面拿它当 /__avatar?v= 的破缓存参数：
    响应是 no-store，但换了头像之后 <img> 的 src 也得变，不然浏览器内存里那张
    还在。没有头像就是 0。
    """
    p = profile_read()
    kind = p.get("头像类型")
    kind = kind if (isinstance(kind, str) and kind in AVATAR_CTYPE) else ""
    ver = 0
    if kind:
        try:
            ver = int(os.path.getmtime(avatar_path()))
        except OSError:
            kind = ""                            # 记着有、盘上没了 → 当没有
    return {"ok": True, "名字": clean_name(p.get("名字")),
            "默认名字": default_name(),
            "头像": bool(kind), "头像版本": ver,
            "问候": bool(p.get("问候", True)),
            "更新于": str(p.get("更新于") or "")}


def display_name() -> str:
    """界面上显示的那个名字：自己设的优先，没设就用这台 Mac 的账户名。"""
    p = profile_view()
    return p["名字"] or p["默认名字"]


def _tmp_path(path):
    """同目录里一个只属于这条线程的临时名。

    **不能是固定的 `path + ".tmp"`**：门户有几十个 handler 线程，两个人同时
    换头像（或者一边存资料一边存头像）会踩同一个文件，后写的那个 replace
    的是别人写了一半的内容。
    """
    return "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())


def _write_json(path, data):
    """先写临时文件再 os.replace。中途断电不会留半份读不出来的 json。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = _tmp_path(path)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _avatar_save(img):
    """写／删头像。返回 (头像类型 或 None, 错误响应 或 None)。

    **只认字节、不落盘**的那一半在这里做完；真正动 avatar.img 的是
    `_avatar_commit`，它排在 profile.json 写成功之后——见 profile_write。
    """
    if img is None:                              # null ＝ 把头像去掉
        return None, None
    if not isinstance(img, dict):
        return None, _bad(T("图片数据不对"))
    raw64 = img.get("数据")
    if not isinstance(raw64, str) or not raw64.strip():
        return None, _bad(T("图片数据不对"))
    if len(raw64) > AVATAR_MAX * 4 // 3 + 1024:  # base64 撑大 4/3，先卡一道免得白解
        return None, _bad(T("头像别超过 {m} MB", m=AVATAR_MAX // 1000 // 1000))
    try:
        blob = base64.b64decode(raw64, validate=True)
    except Exception:
        return None, _bad(T("图片数据不对"))
    if not blob:
        return None, _bad(T("图片是空的"))
    if len(blob) > AVATAR_MAX:
        return None, _bad(T("头像别超过 {m} MB", m=AVATAR_MAX // 1000 // 1000))
    ext = _sniff_img(blob)                       # 认头几个字节，不看前端报的 MIME
    if not ext:
        return None, _bad(T("只收 png / jpg / gif / webp"))
    return (ext, blob), None


def _avatar_commit(got):
    """真正动 avatar.img。`got` 是 _avatar_save 的结果：None ＝删掉，
    (扩展名, 字节) ＝写进去。返回错误响应或 None。"""
    if got is None:
        try:
            os.remove(avatar_path())
        except OSError:
            pass
        return None
    tmp = _tmp_path(avatar_path())
    try:
        os.makedirs(SUPPORT_DIR, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(got[1])
        os.replace(tmp, avatar_path())
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return _bad(T("图片写不进去：{e}", e=e))
    return None


_profile_lock = threading.Lock()


def profile_write(req):
    """POST /__profile。body 是任意子集：给了哪个键就改哪个，其余原样留着。

    **整段读—改—写在一把锁里。** 页面上昵称是防抖保存、头像是另一条 fetch，
    两条几乎同时到是常事；各读各的旧 profile.json 再各写各的，后到的那条
    会把先到的那条改的键抹掉（改完名字换头像＝名字被打回去）。

    **profile.json 先落，avatar.img 后动。** 反过来的话，写图成功、写 json
    失败就留下一张没人认领的头像；而先落 json 的最坏情况是「记着有头像、
    盘上没有」，profile_view 已经认这一种（当没有头像）。
    """
    if not isinstance(req, dict):
        return _bad(T("配置得是一个对象"))
    with _profile_lock:
        p = profile_read()
        if "名字" in req:
            if not isinstance(req["名字"], str):
                return _bad(T("「{k}」类型不对", k="名字", g=gloss("名字")))
            p["名字"] = clean_name(req["名字"])   # 空串合法＝用默认名字
        if "问候" in req:
            p["问候"] = bool(req["问候"])
        got = None
        if "头像" in req:
            got, err = _avatar_save(req["头像"])
            if err:
                return err
            p["头像类型"] = got[0] if got else None
        p["更新于"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            _write_json(profile_path(), p)
        except OSError as e:
            return _bad(T("写失败：{e}", e=e))
        if "头像" in req:
            err = _avatar_commit(got)
            if err:
                return err
        return profile_view()


def avatar_bytes():
    """/__avatar 要发的字节。返回 (字节, content-type) 或 (None, 错误)。"""
    p = profile_read()
    kind = p.get("头像类型")
    ctype = AVATAR_CTYPE.get(kind) if isinstance(kind, str) else None
    if not ctype:
        return None, T("还没设过头像")
    try:
        with open(avatar_path(), "rb") as f:
            return f.read(), ctype
    except OSError:
        return None, T("还没设过头像")


def status():
    """服务状态。**字段只有这几个**：前端要的就是端口、库根和索引进度，
    v19 那批「产出数 / 核心数 / 登记数 / 待补标签数 / 问题数」全是标签层的账，
    随标签层一起撤了；「收件箱未读 / 待打开」两条队列也撤了。

    v21 加了「门禁」一个字段，给前端认版本用。**能加字段的是 status，不是
    pulse**——pulse 那串是被当指纹整串比对的，加一个字段就是死循环。

    5.6 又加两个：「名字」（＝个人资料里的名字，没设就是这台 Mac 的账户名，
    命令行 `amnote status` 用它打招呼）和「接口版本」——调用方靠它知道
    /__map、/__outline、/__profile 这些新路由在不在，不用一条条去试。

    5.7 再加两个：「模式」（单／多）和「笔记本」那张表（接口版本跟着升 3）。
    **老字段一个没动**：「库根」「随手记目录」「索引」报的仍是主笔记本，
    CLI 的 _same_dir 和壳的 adoptExisting 照旧认得出来；「状态」取全体最忙的
    一档，页面那颗小转轮不用管是哪一本在转。"""
    main = nb_main()
    with fulltext.use(main):
        try:
            s = fulltext.index_status()
            idx = {"收录": s.get("收录", 0), "状态": s.get("状态", "")}
            last = s.get("上次同步", "")
        except Exception:
            idx, last = {"收录": 0, "状态": "异常"}, ""
        nd = note_dir(main)
    # 顶层「状态」取全体最忙的一档：有一本在扫就是「扫描中」，
    # 页面那颗小转轮不用去管是哪一本在转
    busy = any(v.state["运行中"] for v in nb_all())
    return {"ok": True,
            "状态": "扫描中" if (busy or idx["状态"] == "同步中") else "就绪",
            "端口": _state["port"], "库根": main.root,
            "上次扫描": last, "索引": idx, "门禁": True,
            "随手记目录": nd,
            "名字": display_name(), "接口版本": 3,
            "模式": nb_mode(), "笔记本": nb_entries()}


_nb_count = {}                        # {vid: {"key": _db_key(), "值": 收录}}


def _nb_doc_count(v):
    """那一本索引里的文档数，**按 (vid, db 指纹) 缓存**。

    /__status 页面每 3 秒问一次，一本就是开一次 sqlite 再 COUNT(*)；N 本乘上
    这个频率，光报个篇数就在不停开库。键用 `_db_key()`（连 -wal 一起看，
    树缓存用的是同一把），db 没动过就直接给上一次数出来的。
    """
    with fulltext.use(v):
        key = _db_key()
        c = _nb_count.get(v.vid)
        if c and c["key"] == key:
            return c["值"]
        try:
            n = fulltext.index_status().get("收录", 0)
        except Exception:
            return 0
    _nb_count[v.vid] = {"key": key, "值": n}
    return n


def nb_entries():
    """`笔记本[]`：/__status 和 /__notebooks 共用一份。

    `状态` 三档：离线（文件夹不在）／扫描中／就绪。`收录` 是那一本索引里的
    文档数——不是全体，页面按本显示篇数。
    """
    dflt = nb_default()
    rows = []
    for v in nb_all():
        n, nd = 0, "随手记"
        if v.online:
            n = _nb_doc_count(v)
            with fulltext.use(v):
                nd = note_dir(v)
        rows.append({"id": v.vid, "名字": v.name, "路径": v.root,
                     "颜色": v.color,
                     "状态": ("离线" if not v.online else
                              "扫描中" if v.state["运行中"] else "就绪"),
                     "收录": n, "随手记目录": nd, "默认": v is dflt})
    return rows


# ── /__notebooks：添加 ／ 移除 ／ 改名 ／ 颜色 ／ 默认 ／ 重新定位 ──
#
# **文件一个字节都不动**（P6）：添加＝登记一个位置，移除＝注销，重新定位＝
# 把同一本指到新位置。索引、废纸篓、历史都留在各自的 .amnote/ 里，
# 移除之后再添加回来，上一次的索引还在。
#
# 落盘只在给了 --notebooks-file 时发生（响应里的 `持久`）。没给就是临时列表：
# 开发脚本 `--root X --support-dir Y` 怎么改都不会碰到用户那份 notebooks.json。


def notebooks_view():
    """GET /__notebooks。页面、CLI、壳都从这儿读列表。"""
    dflt = nb_default()
    return {"ok": True, "笔记本": nb_entries(), "模式": nb_mode(),
            "默认": (dflt.vid if dflt else ""), "持久": bool(NOTEBOOKS_FILE)}


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _nb_name_ok(name, exclude=None):
    """用户给的名字校验一遍。返回 (干净名字, 错误)。

    名字就是路径前缀，所以规矩比昵称严：去首尾空白、不含斜杠（正反都算）和
    NUL、不以 `.` `_` 开头（那两种在库里是「工具的地盘」）、不超过 40 字、
    大小写不敏感唯一。
    """
    n = _nfc(name).strip()
    if not n:
        return "", T("笔记本得有个名字")
    if "/" in n or "\\" in n or "\x00" in n:
        return "", T("名字里不能有斜杠")
    if n.startswith((".", "_")):
        return "", T("名字不能用「.」或「_」开头")
    if len(n) > NB_NAME_MAX:
        return "", T("名字最多 {n} 个字", n=NB_NAME_MAX)
    hit = nb_by_name(n)
    if hit is not None and hit is not exclude:
        return "", T("已经有一个笔记本叫「{n}」了", n=hit.name)
    return n, ""


def _nb_free_name(root):
    """新添一本时的默认名。

    先用文件夹名；撞上已有笔记本就用「父目录名 空格 文件夹名」——
    两个都叫「笔记」的文件夹，用「iCloud 笔记」区分比「笔记 (2)」认得出来。
    再撞才退回加 (2) (3)。
    """
    base = _nb_clean(os.path.basename(root.rstrip(os.sep))) or _nb_clean(root)
    if not base:
        base = "笔记本"
    if nb_by_name(base) is None:
        return base[:NB_NAME_MAX]
    parent = _nb_clean(os.path.basename(os.path.dirname(root.rstrip(os.sep))))
    if parent:
        two = ("%s %s" % (parent, base))[:NB_NAME_MAX]
        if nb_by_name(two) is None:
            return two
    for i in range(2, 100):
        cand = ("%s (%d)" % (base, i))[:NB_NAME_MAX]
        if nb_by_name(cand) is None:
            return cand
    return base[:NB_NAME_MAX - 8] + " " + fulltext.secrets_token()[:4]


def _nb_clean(x):
    """文件夹名 → 能当前缀用的名字：去斜杠，去开头的 `.` `_`。

    名字就是路径前缀，而静态服务对**每一段**都过 `_seg_blocked`——一个叫
    `.笔记` 的文件夹要是原样当了名字，那一本的图片就一张都发不出来。
    """
    n = _nfc(x).strip().replace("/", " ").replace("\\", " ").lstrip("._").strip()
    return n[:NB_NAME_MAX]


def _nb_pick_color():
    """分配一色：用得最少的，一样少的按色盘顺序取前面那个。
    八本以上必然重复——颜色是提示，名字才是身份。"""
    used = {}
    for v in nb_all():
        used[v.color] = used.get(v.color, 0) + 1
    return min(NB_COLORS, key=lambda c: (used.get(c, 0), NB_COLORS.index(c)))


def _nb_conflict(real, exclude=None):
    """这个位置跟已经挂上的那些冲不冲突。返回一句理由，没冲突返回空串。

    嵌套一律拒（在里面、包着都算）：两本套着的话同一份文件会有两个对外路径，
    索引、流水、废纸篓全要认两遍；同一个 realpath 登记两回更糟——两本共用一份
    `fulltext.db` 却各拿一把锁。support dir 也拒，那是 AM·Note 自己的地盘。

    **不看文件夹在不在**：启动时离线的那些要留在列表里（存在与否由
    `_nb_check_root` 另外判），所以这条只比位置。
    """
    sup = os.path.realpath(SUPPORT_DIR)
    if real == sup or real.startswith(sup + os.sep):
        return T("这是 AM·Note 自己的文件夹，不能当笔记本")
    for v in nb_all():
        if v is exclude:
            continue
        other = v.real_root
        if real == other:
            return T("这个文件夹已经是笔记本「{n}」了", n=v.name)
        if real.startswith(other + os.sep):
            return T("这个文件夹已经在「{n}」里面了", n=v.name)
        if other.startswith(real + os.sep):
            return T("「{n}」就在这个文件夹里面", n=v.name)
    return ""


def _nb_check_root(path, exclude=None):
    """一个候选文件夹能不能当笔记本。返回 (绝对路径, 错误)。"""
    raw = _nfc(path).strip()
    if not raw:
        return "", T("要一个文件夹的路径")
    full = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(full):
        return "", T("找不到这个文件夹：{p}", p=full)
    why = _nb_conflict(os.path.realpath(full), exclude=exclude)
    if why:
        return "", why
    try:
        os.listdir(full)                          # 打一次坐实 TCC 授权
    except OSError as e:
        return "", T("读不了：{e}", e=e)
    return full, ""


def _nb_forget(vid):
    """一本被移除／换了位置之后，把按 vid 记着的缓存和撤销表清干净。"""
    with _lock:
        for cache in (_cache, _tree_cache, _marks_cache, _map_cache, _nb_count):
            cache.pop(vid, None)
        for k in [k for k in _trashed if k[0] == vid]:
            _trashed.pop(k, None)


def _nb_result(before, extra=None):
    """一条动作的响应。`变化` 给页面做本地存储的迁移：
    模式从「单」翻到「多」时所有老路径要补上 `主名/` 前缀，翻回去要剥掉。"""
    r = notebooks_view()
    ch = {"从": before, "到": nb_mode(),
          "主名": nb_main().name if nb_main() else ""}
    if extra:
        ch.update(extra)
    r["变化"] = ch
    nb_save()
    return r


def notebooks_do(req):
    """POST /__notebooks。要口令（所有 POST 都要）。"""
    global _nb_default
    act = _nfc(req.get("动作")).strip()
    with _nb_lock:
        before = nb_mode()
        if act == "添加":
            paths = req.get("路径") or []
            if isinstance(paths, str):
                paths = [paths]
            if not isinstance(paths, list) or not paths:
                return _bad(T("要一个文件夹的路径"))
            fresh = []
            for p in paths[:20]:
                full, err = _nb_check_root(p)
                if err:
                    return _bad(err)              # 一条不合法整批不动，好懂
                v = fulltext.register(full, name=_nb_free_name(full),
                                      color=_nb_pick_color(), added=_now_str())
                fresh.append(v)
            for v in fresh:
                # 立刻标「扫描中」：页面添加完马上问 /__status，线程可能还没起来
                v.state["运行中"] = True
                kick_sync(v)
            return _nb_result(before, {"新增": [v.name for v in fresh]})

        if act in ("移除", "改名", "颜色", "默认", "重新定位"):
            v = fulltext.get_vault(_nfc(req.get("id")).strip())
            if v is None:
                return _bad(T("没有这个笔记本"))
        else:
            return _bad(T("不认识这个动作"))

        if act == "移除":
            if len(nb_all()) <= 1:
                return _bad(T("至少要留一个笔记本"))
            fulltext.unregister(v.vid)
            _nb_forget(v.vid)
            if _nb_default == v.vid:
                _nb_default = ""                  # 退回主笔记本
            return _nb_result(before, {"移除": v.name})

        if act == "改名":
            name, err = _nb_name_ok(req.get("名字"), exclude=v)
            if err:
                return _bad(err)
            old = v.name
            v.name = name
            with _lock:
                _map_cache.pop(v.vid, None)       # 地图正文里印着名字
            return _nb_result(before, {"旧名": old, "新名": name})

        if act == "颜色":
            color = _nfc(req.get("颜色")).strip()
            if color not in NB_COLORS:
                return _bad(T("不认识这个颜色"))
            v.color = color
            return _nb_result(before)

        if act == "默认":
            _nb_default = v.vid
            return _nb_result(before)

        full, err = _nb_check_root(req.get("路径"), exclude=v)   # 重新定位
        if err:
            return _bad(err)
        v.point_at(full, name=v.name)
        v.online = True
        fulltext._ensure_dirs(v)
        _nb_forget(v.vid)
        v.state["运行中"] = True
        kick_sync(v)
        return _nb_result(before, {"重新定位": v.name})


# ── notebooks.json：只在给了 --notebooks-file 时读写 ──────────

def nb_save():
    """原子写（tmp ＋ replace），0600。没开持久化就什么都不做。"""
    if not NOTEBOOKS_FILE:
        return False
    dflt = nb_default()
    data = {"版本": NB_VERSION,
            "笔记本": [{"id": v.vid, "名字": v.name, "路径": v.root,
                        "颜色": v.color, "加入": v.added} for v in nb_all()],
            "默认": dflt.vid if dflt else ""}
    tmp = NOTEBOOKS_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(NOTEBOOKS_FILE) or ".", exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)                      # 文件已存在时 O_CREAT 的 mode 不算数
        os.replace(tmp, NOTEBOOKS_FILE)
    except OSError as e:
        print(f"笔记本列表写不了（{NOTEBOOKS_FILE}）：{e}", file=sys.stderr, flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def nb_load_file(path):
    """读 notebooks.json，返回 (条目列表, 默认 id)。

    文件不在＝还没有列表（首启／老用户升级），不是错。**读坏了不当没看见**：
    改名成 notebooks.json.bad-<时间戳> 留证，再按「不在」处理——
    直接覆盖会把用户添过的几本悄悄弄丢。
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return [], ""
    except (OSError, ValueError) as e:
        raw, why = None, e
    else:
        why = "不是一个对象"
    if not isinstance(raw, dict):
        bad = path + ".bad-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(path, bad)
        except OSError:
            pass
        print(f"笔记本列表读不了（{why}），旧文件改名成 {bad}，这次当没有列表。",
              file=sys.stderr, flush=True)
        return [], ""
    rows = []
    for e in (raw.get("笔记本") or []):
        if isinstance(e, dict) and str(e.get("路径") or "").strip():
            rows.append(e)
    return rows, str(raw.get("默认") or "")


def boot_vaults(roots):
    """按启动参数把笔记本列表建起来。返回主笔记本。

    · 给了 --notebooks-file：读它，跟命令行 --root **取并集**（新根加进去并
      写回，名字取文件夹名）。根不在了也留着，标成离线。
    · 没给：只用 --root ／ AMNOTE_VAULT，列表只在内存里。**行为跟 5.6 一样**，
      包括「文件夹不在就 stderr 说一句然后退出」。

    加载的每一条都过一遍 `_nb_conflict`：`/__notebooks` 的添加拦得住嵌套和重复，
    但手改过的 notebooks.json（或者两个 `--root` 写成父子目录）绕得过去，
    而两条同 realpath 的记录共用一份 `fulltext.db` 却各拿一把锁，扫描会互相盖。
    挂不上的那一条丢掉并在 stderr 记一行——**不退出**，剩下的本还能用。
    """
    global _nb_default
    entries, dflt = (nb_load_file(NOTEBOOKS_FILE) if NOTEBOOKS_FILE else ([], ""))
    if not entries and not roots:
        if NOTEBOOKS_FILE:
            print(f"笔记本列表是空的（{NOTEBOOKS_FILE}），命令行也没给 --root。"
                  "先用 --root 指一个文件夹。", file=sys.stderr)
            sys.exit(1)
        fulltext.configure(None)                  # 老路：认 AMNOTE_VAULT，没有就退出
        v = fulltext.main_vault()
        v.color, v.added = NB_COLORS[0], _now_str()
        return v
    for e in entries:
        p = os.path.abspath(os.path.expanduser(_nfc(e.get("路径")).strip()))
        why = _nb_conflict(os.path.realpath(p))
        if why:
            print(f"列表里这一本挂不上（{why}），这次跳过：{p}",
                  file=sys.stderr, flush=True)
            continue
        v = fulltext.register(p, vid=str(e.get("id") or "") or None,
                              color=_nfc(e.get("颜色")).strip(),
                              added=str(e.get("加入") or ""))
        name, err = _nb_name_ok(e.get("名字"), exclude=v)
        v.name = name if not err else _nb_free_name(p)
        if v.color not in NB_COLORS:
            v.color = _nb_pick_color()
    for r in roots:                               # 并集：已经登记过的位置跳过
        p = os.path.abspath(os.path.expanduser(_nfc(r).strip()))
        if not os.path.isdir(p):
            if not NOTEBOOKS_FILE:                # 老口径：给的根不在就直接退出
                print(f"找不到这个文件夹：{p}", file=sys.stderr)
                sys.exit(1)
            print(f"这个笔记本的文件夹现在找不到，先留在列表里：{p}",
                  file=sys.stderr, flush=True)
        real = os.path.realpath(p)
        if any(v.real_root == real for v in nb_all()):
            continue                              # 同一个位置已经在列表里，不用喊
        why = _nb_conflict(real)
        if why:
            print(f"--root 这一个挂不上（{why}），这次跳过：{p}",
                  file=sys.stderr, flush=True)
            continue
        v = fulltext.register(p, name=_nb_free_name(p), color=_nb_pick_color(),
                              added=_now_str())
    if not nb_all():
        print("没有可用的笔记本。请用 --root 指定一个文件夹。", file=sys.stderr)
        sys.exit(1)
    _nb_default = dflt if fulltext.get_vault(dflt) else ""
    nb_save()                                     # 新根写回；没开持久化是空转
    return nb_main()


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

    5.6 起把这一趟请求的署名（X-AMN-Agent）一起记进去，流水上分得出
    「用户自己存的」和「本机某个 AI 助手写的」。
    """
    fulltext.note_portal_write(rel, agent=cur_agent())


def kick_sync(v=None):
    """后台补一轮全文同步（抽正文、记流水、留档）。不给笔记本就全体各起一条线程
    ——各本一把锁、一份状态，互相不用等。fulltext.sync 自带锁，重复叫只会
    登记一次待重跑，不会叠着跑。"""
    for one in ([v] if v is not None else nb_online()):
        threading.Thread(target=_sync_vault, args=(one,), daemon=True).start()


def _sync_vault(v):
    try:
        with fulltext.use(v):
            fulltext.sync()
    finally:
        # 添加一本时先手动把「扫描中」点亮了（线程可能还没排上），这里落下来。
        # 锁还被别人拿着就说明真有一趟在跑，别把它的状态抹了
        if not v.sync_lock.locked():
            v.state["运行中"] = False


_tree_cache = {}                      # {vid: {"key":…, "data":…}}

# ── 「这一篇最近被哪个 Agent 动过」──────────────────────────
#
# 卡片右下角那一行要标 ✦ Claude Code。数据源是变更流水：一条事件的 来源 是
# "Agent" 就带着 代理 字段（见 fulltext._sync_once）。只认**每条路径最近的
# 那一条**——用户后来自己改过，这一篇就不再算 Agent 写的了——而且只认七天内的，
# 再往前标着也没意义。
#
# 只读流水尾部 2000 行：整份流水能到几 MB，而卡片上要的信息都在末尾。

JOURNAL_TAIL = 2000
JOURNAL_TAIL_BYTES = 512 * 1024        # 只把流水的最后这么多字节读进来
AGENT_MARK_TTL = 7 * 86400
_marks_cache = {}                     # {vid: {"key":…, "data":…}}


def _journal_key():
    """缓存键。**要带上「现在是第几个钟头」**：这张表里每条都带 7 天的期限，
    而键只看流水文件的话，一份不再改动的流水会让「三个月前那次 Agent 改动」
    永远留在缓存里，标记过了期也退不下去。"""
    hour = int(time.time() // 3600)
    try:
        st = os.stat(V().journal)
        return (round(st.st_mtime, 2), st.st_size, hour)
    except OSError:
        return (None, hour)


def agent_marks():
    """{路径: Agent 名字}。跟着流水文件的指纹（＋钟点）缓存，随 tree 一起失效。"""
    vid = V().vid
    key = _journal_key()
    with _lock:
        c = _marks_cache.get(vid)
        if c and c["key"] == key and c["data"] is not None:
            return c["data"]
    # 只读尾部：流水能到几 MB，而这里要的信息全在末尾。seek 之后第一行多半是
    # 半条，丢掉；剩下的再按行数收进 deque
    tail = deque(maxlen=JOURNAL_TAIL)
    try:
        with open(V().journal, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            back = min(size, JOURNAL_TAIL_BYTES)
            f.seek(size - back)
            blob = f.read(back)
        if back < size:                          # 从中间切进去的，头一行是半条
            blob = blob.split(b"\n", 1)[-1] if b"\n" in blob else b""
        for line in blob.decode("utf-8", "replace").split("\n"):
            if line:
                tail.append(line)
    except OSError:
        pass
    last = {}
    for line in tail:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        p = e.get("路径")
        if isinstance(p, str) and p:
            last[p] = e                          # 尾部的顺序就是时间顺序
    now = time.time()
    out = {}
    for p, e in last.items():
        if e.get("来源") != fulltext.SRC_AGENT:
            continue
        who = clean_name(e.get("代理"))
        if not who:
            continue
        try:
            at = datetime.strptime(e.get("时间") or "",
                                   "%Y-%m-%d %H:%M:%S").timestamp()
        except (TypeError, ValueError):
            continue
        if 0 <= now - at <= AGENT_MARK_TTL:
            out[p] = who
    with _lock:
        _marks_cache[vid] = {"key": key, "data": out}
    return out


def _db_key():
    """索引库的指纹，当树缓存的键。

    **要连 -wal 一起看**：库是 WAL 模式，写入先落 fulltext.db-wal，
    主库文件的 mtime 要等 checkpoint 才动。只盯 .db 会一直返回过期的树。
    """
    db_path = V().db_path
    out = []
    for p in (db_path, db_path + "-wal"):
        try:
            st = os.stat(p)
            out.append((round(st.st_mtime, 2), st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def tree_view():
    """目录树 ＋ 全部 md/html ＋ 随手记。边栏、列表、最近改动都吃这一份。

    多本时是各本的树合起来的一份：`文档`／`随手记` 的路径带笔记本名前缀、
    每条多一个 `本`；`目录` 是各本的顶层目录各列各的（两本都有「会议」时
    是两条，靠 `本` 分开）；`根文档`／`总数` 求和。**单本时一个字不变。**
    """
    if not nb_multi():
        return tree_one(nb_main())
    docs, dirs, notes = [], [], []
    n_root = 0
    synced = ""
    for v in nb_online():
        with fulltext.use(v):                     # 出去自动还原，别把线程留在最后一本上
            one = tree_one(v)
        if not one.get("ok"):
            continue
        docs += [out_row(v, d) for d in one["文档"]]
        dirs += [dict(d, 本=v.name) for d in one["目录"]]
        notes += [out_row(v, d) for d in one["随手记"]]
        n_root += one.get("根文档", 0)
        synced = max(synced, one.get("生成时间") or "")
    docs.sort(key=lambda x: -x["改于"])
    notes.sort(key=lambda x: -x["改于"])
    return {"ok": True, "目录": dirs, "文档": docs, "附件": [], "随手记": notes,
            "根文档": n_root, "总数": len(docs), "生成时间": synced,
            "笔记本": nb_entries()}


def tree_one(v):
    """一本的树。路径都是库内相对路径，不带前缀——加前缀是 tree_view 的事。

    **数据源是 fulltext.db 的「文档」表**。v20 前是 scan_tags.py 生成的
    产出清单.json，那条路要求文件先打标签才进得来，1187 份没打标签的挂在
    另一张表上，两批合起来才是「文件夹里有什么可读的」。索引没有这道门槛，
    收的就是全部，少一层账。

    只给 md 和 html。每条带 路径 / 标题 / 类型 / 改于 / 预览。
    「附件」那个键留着但恒为空数组：pdf / xlsx / csv 的文字
    照旧进索引搜得到，只是不在门户里列、也不在门户里渲染。

    目录树只把「装着文件的目录」列出来，空壳目录和被跳过的目录自动消失。
    份数是含子目录的累计数。缓存跟着索引库的指纹走，没同步过就不重算。

    5.6 起每篇可能多一个「代理」：这一篇最近一次改动是本机某个 AI 助手写的
    （见 agent_marks）。没有这回事就没有这个键。**流水的指纹也进缓存键**——
    署名只落在流水里，光盯 db 的话卡片上那行 ✦ 会等到下一次索引变动才出现。
    """
    bind(v)
    if not v.online:
        return _bad(T("笔记本「{n}」现在找不到，它的文件夹可能被挪走了", n=v.name))
    key = (_db_key(), _journal_key())
    with _lock:
        c = _tree_cache.get(v.vid)
        if c and c["key"] == key and c["data"]:
            return c["data"]

    try:
        con = fulltext.connect()
        # 只取正文开头：标题在第一个 `# ` 里，整份 md 拉出来白读几 MB
        rows = con.execute("SELECT 路径,类型,mtime,substr(正文,1,4000) FROM 文档 "
                           "WHERE 类型 IN ('md','html')").fetchall()
        synced = fulltext.meta_get(con, "上次同步")
        con.close()
    except Exception as e:
        return _bad(T("fulltext.db 读不了：{e}（跑一次重扫）", e=e))

    generic = tuple(cfg().get("通用标题") or ())
    marks = agent_marks()
    docs = [{"路径": rel, "标题": title_of(rel, head or "", kind, generic),
             "类型": kind, "改于": round(mt or 0, 1),
             "预览": list_preview(head or "", kind)}
            for rel, kind, mt, head in rows]
    for d in docs:
        if marks.get(d["路径"]):
            d["代理"] = marks[d["路径"]]
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
    nd_rel = note_dir(v)
    nd = os.path.join(v.root, nd_rel.replace("/", os.sep))
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
        one = {"路径": nd_rel + "/" + fn,
               "标题": first_heading(raw) or clean_title(fn),
               "改于": round(st.st_mtime, 1), "预览": preview(raw, 200)}
        if marks.get(one["路径"]):
            one["代理"] = marks[one["路径"]]
        notes.append(one)
    notes.sort(key=lambda x: -x["改于"])

    out = {"ok": True, "目录": tree, "文档": docs, "附件": [], "随手记": notes,
           "根文档": sum(1 for r in docs if "/" not in r["路径"]),
           "总数": len(docs), "生成时间": (synced or "")[:16]}
    with _lock:
        _tree_cache[v.vid] = {"key": key, "data": out}
    return out


def rescan(req=None):
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

    多本：body 里带 `nb` 就只扫那一本，不带就逐本扫一遍，返回合并的树。
    """
    one = nb_by_name((req or {}).get("nb") or "")
    which = [one] if one is not None else nb_online()
    try:
        for v in which:
            with fulltext.use(v):
                fulltext.sync()
    except Exception as e:
        return {"ok": False, "错误": f"{type(e).__name__}: {e}"}
    return tree_view()


#: 一次搜索最多从每本里捞这么多行。翻页是「各本先取 offset+n 条再归并再切片」，
#: 而 offset 上限原来是 100000——`offset=100000` 会让**每一本**都把十万行连片段
#: 一起读回来再扔掉。翻到两千条开外没有实际用途，就在这儿封顶（offset 同此上限）。
SEARCH_FETCH_MAX = 2000


def search_view(query):
    """全文搜索，给门户前端和 Agent 共用。正文命中怎么跟标题命中合着排，
    是前端 / 调用方的事；这里只给命中、片段和标题。

    **返回全部类型**（含 csv / xlsx / pdf）。门户那边只显示 md 和 html，
    但 Agent 找一份表格靠的就是这条，服务端不替它过滤。

    v21 起 n 默认 200、上限 500（原来是 60 / 200）：搜「的」这种字在库里命中
    七八百份，60 条截出来的那一段没有意义，前端要的是能一次滚完的一整批。

    5.6 加了筛选和翻页，都是给 Agent 用的（页面只传 q 和 n，那条路一个字没变）：
        dir=工作手记      只搜这个目录底下的
        type=md,pdf      只要这几类
        since=7 ／ 2026-09-01   最近 N 天 ／ 这天之后改过的
        sort=mtime       按改动时间排，不按相关度
        offset=20        翻页（上限 SEARCH_FETCH_MAX，再往后不给翻）

    5.7 多本时扇出再归并：`nb=名字`（逗号可多）限定范围，不给就全体；
    `dir=工作/会议` 这种带前缀的目录隐含了 `nb`。**各本先取 offset+n 条再合并
    排序再切片**——各本只取 n 条的话，翻到第二页会漏掉排在别本后面的那些。
    """
    q = (query.get("q") or [""])[0]

    def _num(k, dflt, lo, hi):
        try:
            return max(lo, min(int((query.get(k) or [str(dflt)])[0]), hi))
        except ValueError:
            return dflt

    n = _num("n", 200, 1, 500)
    off = _num("offset", 0, 0, SEARCH_FETCH_MAX)
    # 逗号可能是中文的——手打参数时常见，不值得为这个回一条错
    raw_types = (query.get("type") or [""])[0].replace("，", ",")
    types = [t.strip() for t in raw_types.split(",") if t.strip()]
    kw = dict(types=types,
              since=(query.get("since") or [""])[0].strip(),
              sort=(query.get("sort") or [""])[0].strip())
    only, sub = resolve_dir((query.get("dir") or [""])[0])
    which = [only] if only is not None else nb_pick(query)
    if only is not None and not only.online:
        return _bad(T("笔记本「{n}」现在找不到，它的文件夹可能被挪走了", n=only.name))
    hits, total, st = [], 0, None
    for v in which:
        with fulltext.use(v):
            try:
                r = fulltext.search(q, limit=min(off + n, SEARCH_FETCH_MAX),
                                    offset=0, subdir=sub, **kw)
            except ValueError:                   # since= 读不懂，见 since_ts
                return _bad(T("since 要写成天数（7）或日期（2026-09-01）"))
        total += r.get("总命中", 0)
        st = st or r.get("状态")
        hits += [out_row(v, h) for h in r.get("结果", [])]
    if len(which) > 1:
        # 档次已经算进分数里了（第一档 +10），同分按改动时间新的在前
        hits.sort(key=lambda h: (h.get("档", 2), -h.get("分数", 0),
                                 _neg_time(h.get("改于"))))
    hits = hits[off:off + n]
    by = {d["路径"]: d["标题"] for d in (tree_view().get("文档") or [])}
    for h in hits:
        h["标题"] = by.get(h["路径"]) or clean_title(os.path.basename(h["路径"]))
    return {"ok": True, "状态": st or {}, "结果": hits,
            "总命中": total, "偏移": off}


def _neg_time(s):
    """按「改于」／「时间」（"%Y-%m-%d %H:%M:%S"）倒序排的键：新的在前，空的垫底。

    时间戳都是同一个格式、同样长，逐字取负就是倒序；空串单独抬到最后一档，
    不然一条读不出时间的记录会顶到列表最前面。
    """
    s = s or ""
    return (0 if s else 1, tuple(-ord(c) for c in s))


def outline_view(query):
    """一份文件的目录。Agent 先看大纲再 /__raw?section= 取那一节，
    不用把整份灌进上下文。

    **从磁盘读最新的那一份**，不读索引：刚写完还没同步的那几秒里，
    大纲得是刚写下去的样子。
    """
    v, rel, err = resolve((query.get("path") or [""])[0])
    if err:
        return _bad(err)
    full, err = _view_full(rel)
    if err:
        return _bad(err)
    low = full.lower()
    kind = ("md" if low.endswith(".md")
            else "html" if low.endswith((".html", ".htm")) else "")
    if not kind:
        return _bad(T("这个格式门户不认"))
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read(fulltext.MD_MAX_BYTES)
    except OSError as e:
        return _bad(T("读不了：{e}", e=e))
    if kind == "md":
        heads = [{"级": h["级"], "文本": h["文本"], "行": h["行"]}
                 for h in fulltext.md_headings(text)]
        head = text
    else:
        # html 没有可靠的大纲（h1/h2 常常只是排版），标题走既有那套抽取
        heads = []
        head = fulltext._extract_html(full)[0]
    return {"ok": True, "路径": out(v, rel),
            "标题": title_of(rel, head, kind, tuple(cfg().get("通用标题") or ())),
            "大纲": heads, "行数": len(_lines_of(text)), "字数": len(text),
            "改于": _mtime_str(full)}


def links_view(query):
    """这一篇指向谁、谁指向它。数据源是索引里的「链接」表（见 extract_links）。

    出链的「存在」：路径链接看那份文件在不在；[[题名]] 看库里有没有同名
    （文件名主干或标题对上）的一篇——按名找本来就允许晚点再建，所以
    「不存在」不等于写错了。
    """
    v, rel, err = resolve((query.get("path") or [""])[0])
    if err:
        return _bad(err)
    full, err = _view_full(rel)
    if err:
        return _bad(err)
    # **[[题名]] 只在同一本里解**：两本都有「发布检查表」时，跨本认亲会把
    # 一条明明写在工作笔记里的链接指到读书笔记那份上去
    docs = tree_one(v).get("文档") or []
    titles = set()
    stems = set()
    for d in docs:
        titles.add((d["标题"] or "").strip().lower())
        stems.add(os.path.splitext(d["路径"].rsplit("/", 1)[-1])[0].lower())
    me = next((d for d in docs if d["路径"] == rel), None)
    mine = {os.path.splitext(rel.rsplit("/", 1)[-1])[0].strip().lower()}
    if me:
        mine.add((me["标题"] or "").strip().lower())
    mine.discard("")

    def _named(name):
        n = (name or "").strip().lower()
        return bool(n) and (n in stems or n in titles)

    try:
        con = fulltext.connect()
        outs = []
        for kind, tgt, txt in con.execute(
                "SELECT 类型,目标,文本 FROM 链接 WHERE 源=?", (rel,)):
            live = (os.path.isfile(fulltext._full(tgt)) if kind == "路径"
                    else _named(tgt))
            outs.append({"目标": out(v, tgt) if kind == "路径" else tgt,
                         "文本": txt or "",
                         "存在": bool(live), "类型": kind})
        # 反链只捞两类行，走 链接_目标 那条索引：路径链接直接按 目标 命中，
        # [[题名]] 那批要在 Python 里比名字（大小写、标题 / 文件名两种写法），
        # 但也只是全库题名链接，不再是整张表
        backs, seen = [], set()
        for src, kind, tgt, txt in con.execute(
                "SELECT 源,类型,目标,文本 FROM 链接 "
                "WHERE (类型='路径' AND 目标=?) OR 类型='题名' ORDER BY 源",
                (rel,)):
            if src == rel:
                continue
            hit = True if kind == "路径" else \
                ((tgt or "").strip().lower() in mine)
            if hit and (src, txt) not in seen:
                seen.add((src, txt))
                backs.append({"源": out(v, src), "文本": txt or ""})
        con.close()
    except Exception as e:
        return _bad(T("fulltext.db 读不了：{e}（跑一次重扫）", e=e))
    return {"ok": True, "路径": out(v, rel), "出链": outs, "反链": backs}


def recent_view(query):
    """最近改过的那些。Agent 的开工第一问：「我不在的时候库里动了什么」。

    数据源是索引的「文档」表（不是流水）：要的是「现在库里最新的那几篇」，
    一篇改了十次也只该出现一次。
    """
    def _num(k, dflt, lo, hi):
        try:
            return max(lo, min(int((query.get(k) or [str(dflt)])[0]), hi))
        except ValueError:
            return dflt

    days = _num("days", 7, 1, 365)
    n = _num("n", 50, 1, 500)
    raw_types = (query.get("type") or [""])[0].replace("，", ",")
    kinds = sorted(set(t.strip().lower() for t in raw_types.split(",") if t.strip()))
    floor = time.time() - days * 86400
    which = nb_pick(query)
    if len(which) != 1:                          # 多本：各取 n 条再按改于归并
        rows = []
        for v in which:
            with fulltext.use(v):
                r = recent_view({"days": [str(days)], "n": [str(n)],
                                 "type": [raw_types], "nb": [v.name]})
            rows += r.get("文档", [])            # 单本那一路已经过 out_row 了
        rows.sort(key=lambda d: _neg_time(d.get("改于")))
        return {"ok": True, "文档": rows[:n]}
    v = which[0]
    bind(v)
    # 筛选和条数都下沉到 SQL：原来是把 365 天内每一篇连 4000 字正文都取回来，
    # 再在 Python 里丢掉——`days=365&n=5` 等于白读全库
    sql = ["SELECT 路径,类型,mtime,大小,substr(正文,1,4000) FROM 文档 WHERE mtime>=?"]
    args = [floor]
    if kinds:
        sql.append("AND lower(类型) IN (%s)" % ",".join("?" * len(kinds)))
        args += kinds
    sql.append("ORDER BY mtime DESC LIMIT ?")
    args.append(n)
    try:
        con = fulltext.connect()
        rows = con.execute(" ".join(sql), args).fetchall()
        con.close()
    except Exception as e:
        return _bad(T("fulltext.db 读不了：{e}（跑一次重扫）", e=e))
    generic = tuple(cfg().get("通用标题") or ())
    marks = agent_marks()
    docs = []
    for rel, kind, mt, size, head in rows:
        one = {"路径": rel,
               "标题": title_of(rel, head or "", kind, generic),
               "类型": kind, "改于": fulltext._mtime_text(mt), "大小": size or 0}
        if marks.get(rel):
            one["代理"] = marks[rel]
        # 单本快路也要过 out_row：`/__recent?nb=工作` 跟不带 nb 的那条扇出必须
        # 给同一形状的路径，不然页面拿到一条 `会议/周会.md` 当键，点开就是 404
        docs.append(out_row(v, one))
        if len(docs) >= n:
            break
    return {"ok": True, "文档": docs}


# ── 库地图 ────────────────────────────────────────────────────
#
# 排版本体在 fulltext（`map_rows` / `map_text`）：命令行在 AM·Note 没开着时
# 画的是同一张图，一处改了两处跟不上的话，Agent 手上那份导航图就会随
# 「门户开没开」变样。这里只负责查索引、缓存和参数钳位。
#
# **只查索引，不读磁盘。** 全库 walk 一遍再逐份开文件，一千份就是几秒；这条路是
# 「每次开工先问一句」的量级，必须几十毫秒回来。代价是刚写完还没同步的那几秒里
# 地图上还是旧的——地图本来就是概览，那几秒不重要（要最新的走 /__outline）。

MAP_MAX = fulltext.MAP_MAX
MAP_MAX_CAP = fulltext.MAP_MAX_CAP
MAP_DEPTH_CAP = fulltext.MAP_DEPTH_CAP
MAP_BUDGET = fulltext.MAP_BUDGET
MAP_BUDGET_CAP = fulltext.MAP_BUDGET_CAP
MAP_FILE = fulltext.MAP_FILE
MAP_NOTE = fulltext.MAP_NOTE

# 一张图画一遍要扫全库正文的前 4000 字，而设置面板、`amnote map` 和导出
# 会连着问同一组参数。跟树一个套路：索引没动、参数没变就发上一张
_map_cache = {}                       # {vid: {"key":…, "data":…}}


def map_payload(v=None, sub="", per=MAP_MAX, depth=0, budget=MAP_BUDGET):
    """一本的地图（成功时是 §1.6 那个形状，失败时是 _bad(...)）。

    多本时地图正文里的路径也带前缀（`工作/会议/周会.md`）：Agent 照着地图里
    那一行去 `amnote read` 就该直接读得到，还要自己拼一次前缀就白给了。
    """
    v = v or V()
    bind(v)
    key = (_db_key(), sub, per, depth, budget, nb_multi())
    with _lock:
        c = _map_cache.get(v.vid)
        if c and c["key"] == key and c["data"] is not None:
            return c["data"]
    try:
        con = fulltext.connect()
        rows = fulltext.map_rows(con)
        con.close()
    except Exception as e:
        return _bad(T("fulltext.db 读不了：{e}（跑一次重扫）", e=e))
    if nb_multi():
        root_name = v.name
        rows = [(out(v, r[0]),) + tuple(r[1:]) for r in rows]
    else:
        root_name = os.path.basename(v.real_root.rstrip(os.sep)) or v.real_root
    data = fulltext.map_text(rows, root_name, sub=sub, per=per,
                             depth=depth, budget=budget)
    with _lock:
        _map_cache[v.vid] = {"key": key, "data": data}
    return data


def map_view(query):
    """GET /__map。参数见 fulltext.map_text；`depth` 给 0 或不给＝不限深度。

    多本且没限定哪一本时**一本一节**（各自一个 `# 笔记库地图 · 名字` 开头），
    预算按本数平分——不然第一本就能把整份额度吃光。
    """
    def _num(k, dflt, lo, hi):
        try:
            return max(lo, min(int((query.get(k) or [str(dflt)])[0]), hi))
        except ValueError:
            return dflt

    per = _num("max", MAP_MAX, 1, MAP_MAX_CAP)
    depth = _num("depth", 0, 0, MAP_DEPTH_CAP)
    budget = _num("budget", MAP_BUDGET, 1000, MAP_BUDGET_CAP)
    raw_dir = (query.get("dir") or [""])[0]
    only, sub = resolve_dir(raw_dir)
    if only is None:
        picked = nb_pick(query)
        raw_nb = (query.get("nb") or [""])[0].strip()
        if raw_nb and not picked:
            # `nb=` 给了但一个名字都不认识。原来落到 _map_join([]) 上，
            # 报的是「还没有添加任何笔记本」——话不对，库明明在
            return _bad(T("没有叫「{n}」这个名字的笔记本", n=raw_nb))
        if len(picked) == 1:
            only = picked[0]
        elif len(picked) < len(nb_online()):
            # nb= 挑了几本（但不是全体）：几本各一节
            return _map_join(picked, sub, per, depth, budget)
    if only is not None:
        return map_payload(only, sub=sub, per=per, depth=depth, budget=budget)
    if not nb_multi():
        return map_payload(nb_main(), sub=sub, per=per, depth=depth, budget=budget)
    return _map_join(nb_online(), sub, per, depth, budget)


def _map_join(which, sub, per, depth, budget):
    """几本的地图接成一份。节标题就是各本自己那行 `# 笔记库地图 · 名字`。"""
    if not which:
        return _bad(T("还没有添加任何笔记本"))
    share = max(1000, budget // max(1, len(which)))
    parts, n_docs, n_dirs, cut = [], 0, 0, False
    for v in which:
        with fulltext.use(v):
            d = map_payload(v, sub=sub, per=per, depth=depth, budget=share)
        if not d.get("ok"):
            continue
        parts.append(d["地图"])
        n_docs += d.get("篇数", 0)
        n_dirs += d.get("目录数", 0)
        cut = cut or bool(d.get("截断"))
    return {"ok": True, "地图": "\n".join(parts), "篇数": n_docs,
            "目录数": n_dirs, "截断": cut,
            "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M")}


def meta_view(query):
    """一份文件的基本面。Agent 拿它代替自己去 stat ＋ 读文件头。"""
    v, rel, err = resolve((query.get("path") or [""])[0])
    if err:
        return _bad(err)
    full, err = _view_full(rel)
    if err:
        return _bad(err)
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
    r = {"ok": True, "路径": out(v, rel), "类型": kind,
         "标题": title_of(rel, head, kind, tuple(cfg().get("通用标题") or ()))}
    try:
        st = os.stat(full)
        r["改于"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        r["大小"] = st.st_size                    # 字节
    except OSError:
        r["改于"], r["大小"] = "", 0
    return r


# ── 接入向导：把这个库接给本机的 AI 助手 ─────────────────────
#
# 设置面板「Agent」那一段的后端。四件事，每件都是一次性的：
#   cli         ~/.local/bin/amnote → src/amnote 的软链接（命令行能直接叫）
#   skill       ~/.claude/skills/amnote/SKILL.md（Claude Code 认得的技能包）
#   agents_md   <库根>/AGENTS.md（在库目录里跑的 agent 开工先读它）
#   map_export  <库根>/库地图.md（§1.6 那份地图落成文件，给不联服务的场合）
#
# 家目录走 HOME_DIR（env AMNOTE_HOME），测试时指到 scratch——这四件事里有三件
# 写在用户的家目录里，写错地方是要在真人的 ~/.claude 里留垃圾的。

AGENT_DIR = os.path.join(HERE, "agent")          # 模板：SKILL.md / AGENTS.md
AMNOTE_BIN = os.path.join(HERE, "amnote")        # 命令行包装脚本（dev 时是 src/amnote）
AGENTS_FILE = "AGENTS.md"
SKILL_MARK = "amnote-skill"                      # 我们写的那份 SKILL.md 的标记
SKILL_MARK_LINES = 8                             # 标记在 frontmatter 之后，前几行里找
SKILL_VER_RE = re.compile(SKILL_MARK + r"\s+v(\d+)")


def _cli_link():
    return os.path.join(HOME_DIR, ".local", "bin", "amnote")


def _skill_ver(path):
    """一份 SKILL.md 的版本号：前几行里那句 `<!-- amnote-skill vN -->` 的 N。

    没装、读不了、或者那位置上是别人写的一份（没有标记）一律 0。设置面板拿
    「已装的」跟「随这一版发的」比一下，落后了就把按钮换成「更新 Skill」——
    5.7 把 skill 升到 v2（首段改成「先 amnote notebooks」），老用户手上那份 v1
    会让 Agent 在多笔记本的库里按不带前缀的路径去找文件。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = "".join([f.readline() for _ in range(SKILL_MARK_LINES)])
    except OSError:
        return 0
    m = SKILL_VER_RE.search(head)
    return int(m.group(1)) if m else 0


def _skill_file():
    return os.path.join(HOME_DIR, ".claude", "skills", "amnote", "SKILL.md")


def _vault_file(name, v=None):
    return os.path.join((v or V()).root, name)


def _setup_row(name, v):
    """库根里那两份文件（AGENTS.md ／ 库地图.md）在某一本里的状态。"""
    p = _vault_file(name, v)
    row = {"路径": p, "已存在": os.path.lexists(p)}
    if nb_multi():
        row["本"] = v.name
    return row


def agent_setup_view(v=None):
    """GET /__agent_setup：四样东西各自装没装、路径是什么，外加两段能拷走的配置。

    命令行和 skill 是本机一份；AGENTS.md 和库地图落在库根里，所以**多本时这两项
    变成按本的数组**（页面按本各列一行）。单本时形状一个字不变。

    `skill` 另带 `版本`（已装那份的 N，没装是 0）和 `最新`（这一版随包发的 N）：
    页面在 `版本 < 最新` 时把按钮文案换成「更新 Skill」。
    """
    v = v or V()
    link = _cli_link()
    try:
        linked = (os.path.islink(link)
                  and os.path.realpath(link) == os.path.realpath(AMNOTE_BIN))
    except OSError:
        linked = False
    if nb_multi():
        agents = [_setup_row(AGENTS_FILE, one) for one in nb_all()]
        maps = [_setup_row(MAP_FILE, one) for one in nb_all()]
    else:
        agents = _setup_row(AGENTS_FILE, v)
        maps = _setup_row(MAP_FILE, v)
    return {"ok": True,
            "命令行": {"路径": AMNOTE_BIN, "链接路径": link, "已链接": bool(linked)},
            "skill": {"路径": _skill_file(),
                      "已安装": os.path.isfile(_skill_file()),
                      "版本": _skill_ver(_skill_file()),
                      "最新": _skill_ver(os.path.join(AGENT_DIR, "SKILL.md"))},
            "agents_md": agents,
            "地图文件": maps,
            "mcp命令": "claude mcp add amnote -- %s mcp" % shlex.quote(AMNOTE_BIN),
            "codex配置": ("[mcp_servers.amnote]\ncommand = %s\n"
                          "args = [\"mcp\"]\n" % json.dumps(AMNOTE_BIN))}


def _tpl(name):
    """读一份模板并把 {{AMNOTE_BIN}} 换成真路径。返回 (正文, 错误)。

    路径是**给 shell 抄的**，一律 shlex.quote：app 可以被拖到
    `/Users/我的 "笔记" 工具/` 这种文件夹里，裸着写进 SKILL.md 里的命令，
    Agent 照抄一句就跑到别的地方去了。
    """
    p = os.path.join(AGENT_DIR, name)
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().replace("{{AMNOTE_BIN}}",
                                    shlex.quote(AMNOTE_BIN)), ""
    except OSError:
        return "", T("缺一份模板：{p}", p=p)


def _write_text(path, text):
    """写一份文本文件，先 .tmp 再改名。返回错误响应或 None。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        return _bad(T("写不进去：{e}", e=e))
    return None


def _setup_cli():
    """~/.local/bin/amnote → src/amnote。指到别处的旧链接直接换掉；
    是一个普通文件就不动它——那多半是用户自己放的东西，不该被我们盖掉。"""
    link = _cli_link()
    if not os.path.isfile(AMNOTE_BIN):
        return _bad(T("缺一份模板：{p}", p=AMNOTE_BIN))
    try:
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.path.lexists(link):
            if not os.path.islink(link):
                return _bad(T("那个位置已经有一份文件了，先挪开"))
            os.remove(link)
        os.symlink(AMNOTE_BIN, link)
    except OSError as e:
        return _bad(T("写不进去：{e}", e=e))
    return T("命令行工具装好了：{p}", p=link)


def _setup_skill():
    """写 Claude Code 的技能包。已经有一份、但不是我们生成的就不覆盖。"""
    text, err = _tpl("SKILL.md")
    if err:
        return _bad(err)
    dest = _skill_file()
    if os.path.lexists(dest):
        try:
            with open(dest, encoding="utf-8", errors="replace") as f:
                head = "".join([f.readline() for _ in range(SKILL_MARK_LINES)])
        except OSError as e:
            return _bad(T("读不了：{e}", e=e))
        if SKILL_MARK not in head:
            return _bad(T("那个位置已经有一份文件了，先挪开"))
    bad = _write_text(dest, text)
    return bad or T("Skill 装好了：{p}", p=dest)


def _setup_agents_md():
    """在库根写 AGENTS.md。已经有就不动——那是用户自己写给 agent 的话。"""
    text, err = _tpl(AGENTS_FILE)
    if err:
        return _bad(err)
    dest = _vault_file(AGENTS_FILE)
    if os.path.lexists(dest):
        return _bad(T("那个位置已经有一份文件了，先挪开"))
    note_portal_write(AGENTS_FILE)               # 别让下一趟同步记成「外部新增」
    bad = _write_text(dest, text)
    if bad:
        return bad
    kick_sync()
    return T("AGENTS.md 写好了：{p}", p=dest)


def _setup_map():
    """把地图导成库根的一份 md。可以反复覆盖，首行留一句「这是生成的」。"""
    d = map_payload(V())
    if not d.get("ok"):
        return d
    dest = _vault_file(MAP_FILE)
    note_portal_write(MAP_FILE)
    bad = _write_text(dest, MAP_NOTE + "\n" + d["地图"])
    if bad:
        return bad
    kick_sync()
    return T("库地图导出好了，{n} 篇：{p}", n=d["篇数"], p=dest)


def agent_setup_do(req):
    """POST /__agent_setup。响应＝GET 的形状再加一句「结果」。"""
    act = str(req.get("动作") or "").strip()
    fn = {"cli": _setup_cli, "skill": _setup_skill,
          "agents_md": _setup_agents_md, "map_export": _setup_map}.get(act)
    if not fn:
        return _bad(T("不认识这个动作"))
    # AGENTS.md / 库地图 落在哪一本：`nb` 说了算，不给就是主笔记本
    v, err = nb_arg(req.get("nb") or req.get("笔记本"))
    if err:
        return _bad(err)
    bind(v)
    r = fn()
    if isinstance(r, dict):                      # _bad(...) 原样上抛
        return r
    payload = agent_setup_view(v)
    payload["结果"] = str(r)
    return payload


def changes_view(query):
    """变更流水原样给出去，Agent 查「这几天库里动了什么」用。

    多本时是几份流水合起来的一份，按 `时间` 倒序，每条多一个 `本`。
    **`序号` 是各本自己的号，跨本不唯一**——`since=` 也是按本各自应用的，
    要按序号取增量就一次只问一本（`nb=`）。
    """
    try:
        since = int((query.get("since") or ["0"])[0])
        n = max(1, min(int((query.get("n") or ["200"])[0]), 1000))
    except ValueError:
        since, n = 0, 200
    which = nb_pick(query)
    if len(which) == 1:
        # 单本快路一样过 out_row（`/__recent` 那条同理）：`?nb=工作` 跟扇出
        # 那一路必须给同一形状的路径，不然调用方按 `本` 分组时少一半
        with fulltext.use(which[0]):
            return {"ok": True,
                    "流水": [out_row(which[0], e)
                             for e in fulltext.journal_read(since, n)]}
    rows = []
    for v in which:
        with fulltext.use(v):
            rows += [out_row(v, e) for e in fulltext.journal_read(since, n)]
    rows.sort(key=lambda e: _neg_time(e.get("时间")))
    return {"ok": True, "流水": rows[:n]}


def archive_view(query):
    """读一份留档（门户编辑备份和外部覆写留档同一个目录）。只读。

    留档在各本自己的 `.amnote/backups/` 里，名字只是把路径压平的一串，
    不带笔记本。所以多本时要 `nb=名字` 说清是哪一本的——不给就是主笔记本
    （`/__changes` 每条都带 `本`，调用方照抄那一个就行）。
    """
    name = (query.get("name") or [""])[0]
    v, err = nb_arg((query.get("nb") or [""])[0])
    if err:
        return _bad(err)
    bind(v)
    text = fulltext.archive_read(name)
    if text is None:
        return _bad(T("没有这份留档"))
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


# ── /__locate：绝对路径 → 对外路径 ──────────────────────────
#
# 访达双击一份 md（或者「打开方式」、拖到 Dock）时，壳投过来的是磁盘上的绝对
# 路径。页面自己拿 `库根` 做前缀匹配是不够的：库落在 `/private/…`、或者路上有
# 一段软链接时，两边标准化的口径不一样，前缀对不上，文稿就静默不开。
# 这条把匹配挪到服务端做一次——两边都 realpath，各本 real_root 取最长前缀，
# 回一个页面直接能当键用的对外路径。
# **免口令**：只做字符串换算，不 stat、不读内容，泄不出任何库里的东西。


def locate_view(query):
    """`GET /__locate?abs=/Users/…/工作笔记/会议/周会.md`
    → `{ok:true, 路径:"工作/会议/周会.md", 本:"工作"}`。

    · 不在任何一本里 → `{ok:false, 代码:"outside"}`（页面照旧走库外只读那条）；
    · 落在 `.amnote`、点开头的段、`_编辑备份`、`__pycache__` 里 →
      `{ok:false, 代码:"bad_path"}`——那几处静态和编辑都不给碰，给出一个「路径」
      只会让页面开一份注定打不开的文稿；
    · 那一本现在离线 → `{ok:false, 代码:"offline"}`。
    单笔记本模式下不加前缀（跟 `out()` 一个口径），`本` 照样报主笔记本的名字。
    """
    p = (query.get("abs") or [""])[0]
    if not p or "\x00" in p:
        return _bad(T("要一个绝对路径"))
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        return _bad(T("要一个绝对路径"))
    real = os.path.realpath(p)
    hit, rel = None, ""
    for v in nb_all():
        root = v.real_root
        if real == root:
            cand = ""
        elif real.startswith(root + os.sep):
            cand = os.path.relpath(real, root)
        else:
            continue
        # 取**最长**前缀：两本不该嵌套（`_nb_conflict` 拦着），但「重新定位」
        # 换位置的那一瞬间可能重合，取长的那一本总不会认错。
        if hit is None or len(root) > len(hit.real_root):
            hit, rel = v, cand
    if hit is None:
        return _bad(T("这份不在任何一个笔记本里"))
    if not hit.online:
        return _bad(T("笔记本「{n}」现在找不到，它的文件夹可能被挪走了", n=hit.name))
    if any(_seg_blocked(seg) for seg in (rel.split(os.sep) if rel else [])):
        return _bad(T("路径不合法"))
    rel = rel.replace(os.sep, "/")
    return {"ok": True, "本": hit.name,
            "路径": out(hit, rel) or (hit.name if nb_multi() else "")}


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
        return _bad(T("要一个绝对路径"))
    full = os.path.realpath(os.path.expanduser(p))
    if not full.lower().endswith(".md"):
        return _bad(T("只能打开 md"))
    if not os.path.isfile(full):
        return _bad(T("这份文件不在了"))
    for v in nb_all():                           # 任何一本里的都算库内
        if full == v.real_root or full.startswith(v.real_root + os.sep):
            return _bad(T("这份在库里，按库内文档打开"))
    try:
        size = os.path.getsize(full)
    except OSError as e:
        return _bad(T("读不了：{e}", e=e))
    if size > EXT_MAX:
        return _bad(T("这份 {n} MB，上限 {m} MB",
                      n=size // 1048576, m=EXT_MAX // 1048576))
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
        return _bad(T("这份没登记过，重新拖一次"))
    try:
        with open(ent["路径"], encoding="utf-8", errors="replace") as f:
            text = f.read(EXT_MAX)
        st = os.stat(ent["路径"])
    except OSError as e:
        return _bad(T("读不了：{e}", e=e))
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
        return None, T("这份没登记过，重新拖一次")
    if not rel or "\x00" in rel or os.path.isabs(rel):
        return None, T("路径不合法")
    base = os.path.realpath(os.path.dirname(ent["路径"]))
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep):
        return None, T("这张图不在文档旁边")
    ctype = EXT_IMG_EXT.get(os.path.splitext(full)[1].lower())
    if not ctype:
        return None, T("只认图片")
    if not os.path.isfile(full):
        return None, T("这张图不在了")
    try:
        if os.path.getsize(full) > EXT_ASSET_MAX:
            return None, T("这张图太大了")
        with open(full, "rb") as f:
            return f.read(), ctype
    except OSError as e:
        return None, T("读不了：{e}", e=e)


# ── 阅读字体：新细明体（PMingLiU）───────────────────────────────
#
# 繁體中文（香港）那一档阅读字体要的是新细明体。macOS 不自带，但装了 Office 的
# 机器上每个 app 包里都躺着一份 mingliu.ttc（22.7 MB，三面共用同一张 22 MB 的
# glyf：MingLiU ／ PMingLiU ／ MingLiU_HKSCS）。页面的 @font-face 先用 local()
# 碰本机装好的，碰不到才落到这条路由，由服务端把 .ttc 里那一面抽成独立 TTF
# 当 web font 发出去。
#
#     GET /__fonts          探一探有没有、来自哪儿（user ／ system ／ office）。
#                           轻——只读表目录和 name 表，不碰那 22 MB 的 glyf。
#     GET /__font/pmingliu  真把字体发出去（HEAD 也认）。**第一次打它才抽**，
#                           抽完落 ~/Library/Application Support/AMNote/fonts/，
#                           之后直接从缓存出。启动路径上一个字节都不读——
#                           为一个未必用得上的字体多花两秒开机时间不值当。
#
# 两条都免 token：CSS 里的 url() 带不了自定义头，跟库内图片一个道理；Host ／
# Origin 那两道门照旧（_gate 每个请求都过）。**只读盘上的字体、只写自己那个
# 缓存目录**，库里一个字节都不碰。抽字体这一路整个包在 try 里：字体是锦上添花，
# 出什么岔子都只该退化成「没有这个字体」，不能把服务带下去。

# 跟着 --support-dir 走（默认值跟以前一样是 ~/Library/Application Support/AMNote/
# fonts）：测试时把支撑目录挪去 scratch，字体缓存不能还留在用户真正那一份里。
FONT_CACHE_DIR = os.path.join(SUPPORT_DIR, "fonts")
PMING_TTF = os.path.join(FONT_CACHE_DIR, "PMingLiU.ttf")
PMING_JSON = os.path.join(FONT_CACHE_DIR, "PMingLiU.json")   # 来源路径＋大小＋mtime

# 查找顺序（5.5 契约 §5）：用户字体 → 系统字体 → 从 Office 包里借。每一档里
# 先看独立的 PMingLiU.ttf，再看 mingliu.ttc；文件名不分大小写。
FONT_DIRS = (
    ("user", os.path.expanduser("~/Library/Fonts")),
    ("system", "/Library/Fonts"),
    ("system", "/System/Library/Fonts"),
    ("system", "/System/Library/Fonts/Supplemental"),
)
FONT_NAMES = ("pmingliu.ttf", "mingliu.ttc")
OFFICE_APPS = ("Word", "Excel", "PowerPoint", "Outlook", "OneNote")
OFFICE_TTC = "/Applications/Microsoft %s.app/Contents/Resources/DFonts/mingliu.ttc"
PMING_PS_NAME = "PMingLiU"                # name 表 nameID 6（PostScript 名）
FONT_PROBE_TTL = 60.0                     # 探测结果缓存这么多秒

_probe_lock = threading.Lock()
_extract_lock = threading.Lock()
_font_probe = {"at": 0.0, "值": None}


def _sfnt_dir(f, off):
    """读一个 sfnt 的表目录。返回 (sfntVersion, [(tag, 偏移, 长度), …])。"""
    f.seek(off)
    head = f.read(12)
    if len(head) < 12:
        raise ValueError("sfnt 头读不全")
    sv, n = struct.unpack(">IH", head[:6])
    if not 1 <= n <= 512:
        raise ValueError("表数目不像话：%d" % n)
    raw = f.read(16 * n)
    if len(raw) < 16 * n:
        raise ValueError("表目录读不全")
    out = []
    for i in range(n):
        tag, _cs, o, l = struct.unpack(">4sIII", raw[16 * i:16 * i + 16])
        out.append((tag, o, l))
    return sv, out


def _name_table(f, off, length):
    """name 表里的 {nameID: 文本}。

    同一个 nameID 有好几条（Mac／Windows × 中英文），要的是**英文那条**：
    新细明体的 nameID 1 在简繁语言里是「新細明體」，拿它跟 PostScript 名比
    永远对不上。所以英文（Mac lid=0 ／ Windows lid=1033）优先覆盖。
    """
    f.seek(off)
    raw = f.read(min(length, 1 << 20))
    if len(raw) < 6:
        return {}
    _fmt, cnt, str_off = struct.unpack(">HHH", raw[:6])
    out = {}
    for i in range(cnt):
        p = 6 + 12 * i
        if p + 12 > len(raw):
            break
        pid, _eid, lid, nid, ln, o = struct.unpack(">6H", raw[p:p + 12])
        s = raw[str_off + o: str_off + o + ln]
        if len(s) < ln:
            continue
        try:
            txt = s.decode("utf-16-be") if pid in (0, 3) else s.decode("mac-roman")
        except (UnicodeDecodeError, LookupError):
            continue
        if nid not in out or lid in (0, 1033):
            out[nid] = txt
    return out


def _face_names(f, off):
    """一面字体的 (sfntVersion, 表目录, {nameID: 文本})。"""
    sv, tabs = _sfnt_dir(f, off)
    for tag, o, l in tabs:
        if tag == b"name":
            return sv, tabs, _name_table(f, o, l)
    return sv, tabs, {}


def _is_pmingliu(names):
    """这一面是不是新细明体：认 PostScript 名（nameID 6），退回英文族名（1）。"""
    return ((names.get(6) or "").strip() == PMING_PS_NAME
            or (names.get(1) or "").strip() == PMING_PS_NAME)


def _pming_face(path):
    """这份字体文件里新细明体是第几面。

    独立的 ttf 返回 -1（整份直接就能用），.ttc 返回面序号，不是新细明体
    返回 None。只读文件头、表目录和 name 表，那 22 MB 的 glyf 一个字节不碰。
    """
    with open(path, "rb") as f:
        if f.read(4) == b"ttcf":
            f.seek(8)
            (n,) = struct.unpack(">I", f.read(4))
            if not 1 <= n <= 64:
                return None
            offs = struct.unpack(">%dI" % n, f.read(4 * n))
            for i, off in enumerate(offs):
                try:
                    _sv, _tabs, names = _face_names(f, off)
                except (ValueError, struct.error, OSError):
                    continue
                if _is_pmingliu(names):
                    return i
            return None
        _sv, _tabs, names = _face_names(f, 0)
        return -1 if _is_pmingliu(names) else None


def _find_pmingliu():
    """按契约的顺序找一份新细明体。返回 (来源, 路径, 面序号)。

    来源是 "user" ／ "system" ／ "office"，跟 /__fonts 里那个字段一一对应；
    一份都没找到返回 (None, "", None)。
    """
    for where, d in FONT_DIRS:
        try:
            with os.scandir(d) as it:
                have = {e.name.lower(): e.name for e in it if not e.is_dir()}
        except OSError:
            continue
        for want in FONT_NAMES:
            if want not in have:
                continue
            p = os.path.join(d, have[want])
            try:
                idx = _pming_face(p)
            except (OSError, ValueError, struct.error):
                continue
            if idx is not None:
                return where, p, idx
    for app in OFFICE_APPS:
        p = OFFICE_TTC % app
        if not os.path.isfile(p):
            continue
        try:
            idx = _pming_face(p)
        except (OSError, ValueError, struct.error):
            continue
        if idx is not None:
            return "office", p, idx
    return None, "", None


def probe_pmingliu(max_age=FONT_PROBE_TTL):
    """探测结果，带一分钟的缓存。任何异常都当「没有」，只往 stderr 记一笔。"""
    with _probe_lock:
        hit = _font_probe["值"]
        if hit is not None and time.time() - _font_probe["at"] < max_age:
            return hit
    try:
        hit = _find_pmingliu()
    except Exception as e:
        print(f"找新细明体时出错：{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        hit = (None, "", None)
    with _probe_lock:
        _font_probe["值"] = hit
        _font_probe["at"] = time.time()
    return hit


def fonts_view():
    """/__fonts：这台机器上新细明体拿不拿得到、来自哪儿。免 token。"""
    where, _p, _i = probe_pmingliu()
    return {"pmingliu": {"available": bool(where), "source": where}}


def _sum32(b: bytes) -> int:
    """sfnt 的校验和：按大端 uint32 逐个相加取低 32 位，不足 4 字节的补零。"""
    if len(b) % 4:
        b = b + b"\0" * (-len(b) % 4)
    a = array.array("I")
    a.frombytes(b)
    if sys.byteorder == "little":
        a.byteswap()
    return sum(a) & 0xFFFFFFFF


def _build_sfnt(data: bytes, sv: int, tabs) -> bytes:
    """把一面的那些表拼成一份独立的 sfnt。

    表目录按 tag 排序（规范要求），numTables ／ searchRange ／ entrySelector ／
    rangeShift 全部重算；每张表 4 字节对齐、尾巴补零，校验和逐表重算；
    最后按整份文件的校验和填 head.checkSumAdjustment——算的时候那个字段本身
    必须是 0，所以先清零、拼完再回填。
    """
    tabs = sorted(tabs, key=lambda t: t[0])
    n = len(tabs)
    es = max(n.bit_length() - 1, 0)
    sr = 16 * (1 << es)
    out = bytearray(struct.pack(">IHHHH", sv, n, sr, es, 16 * n - sr))
    dir_at = len(out)                            # 12＋16n 天然是 4 的倍数
    out += b"\0" * (16 * n)
    recs = []
    head_at = None
    for tag, off, ln in tabs:
        blob = data[off:off + ln]
        if len(blob) != ln:
            raise ValueError("表 %s 读不全" % tag.decode("latin-1", "replace"))
        if tag == b"head":
            if ln < 54:
                raise ValueError("head 表太短")
            blob = blob[:8] + b"\0\0\0\0" + blob[12:]
            head_at = len(out)
        recs.append((tag, _sum32(blob), len(out), ln))
        out += blob + b"\0" * (-ln % 4)
    if head_at is None:
        raise ValueError("没有 head 表")
    for i, (tag, cs, at, ln) in enumerate(recs):
        struct.pack_into(">4sIII", out, dir_at + 16 * i, tag, cs, at, ln)
    struct.pack_into(">I", out, head_at + 8,
                     (0xB1B0AFBA - _sum32(bytes(out))) & 0xFFFFFFFF)
    return bytes(out)


def _extract_pmingliu(path: str, idx: int) -> bytes:
    """从 .ttc 里抽第 idx 面，写成独立 TTF 的字节。整份读进内存（22 MB）。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"ttcf":
        raise ValueError("不是 ttc")
    (n,) = struct.unpack(">I", data[8:12])
    if not 0 <= idx < n:
        raise ValueError("没有第 %d 面" % idx)
    (off,) = struct.unpack(">I", data[12 + 4 * idx: 16 + 4 * idx])
    sv, cnt = struct.unpack(">IH", data[off:off + 6])
    tabs = [struct.unpack(">4sIII", data[off + 12 + 16 * i: off + 28 + 16 * i])
            for i in range(cnt)]
    return _build_sfnt(data, sv, [(t, o, l) for t, _c, o, l in tabs])


def _cache_ok(src_path: str) -> bool:
    """缓存里那份还算数吗。

    sidecar 记着来源路径、大小、mtime，三样跟盘上现在这份对得上、抽出来那份
    ttf 也还在且大小没变，才算数。Office 升过级、换了一份 .ttc，这里就对不上，
    自动重抽一遍。
    """
    try:
        with open(PMING_JSON, encoding="utf-8") as f:
            side = json.load(f)
        st = os.stat(src_path)
        out = os.stat(PMING_TTF)
    except (OSError, ValueError):
        return False
    return (side.get("来源") == src_path
            and side.get("大小") == st.st_size
            and side.get("改于") == round(st.st_mtime, 3)
            and side.get("字节") == out.st_size)


def pmingliu_file():
    """/__font/pmingliu 要发的那份独立 TTF 的路径；拿不到返回空串。

    盘上本来就是独立一份 ttf 的直接给路径；.ttc 第一次要抽一遍（22 MB，两三秒），
    抽完落缓存，之后直接从缓存出。**抽的时候上一把锁**：两个请求同时进来只抽
    一次，先写 .tmp 再 os.replace，中途出岔子不会留半截字体文件。
    """
    where, path, idx = probe_pmingliu()
    if not where:
        return ""
    if idx == -1:                                # 盘上本来就是独立的一份
        return path
    with _extract_lock:
        try:
            if _cache_ok(path):
                return PMING_TTF
            blob = _extract_pmingliu(path, idx)
            st = os.stat(path)
            os.makedirs(FONT_CACHE_DIR, exist_ok=True)
            tmp = PMING_TTF + ".tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
            os.replace(tmp, PMING_TTF)
            with open(PMING_JSON, "w", encoding="utf-8") as f:
                json.dump({"来源": path, "大小": st.st_size,
                           "改于": round(st.st_mtime, 3),
                           "字节": len(blob), "面": idx},
                          f, ensure_ascii=False, indent=2)
            return PMING_TTF
        except Exception as e:
            print(f"抽新细明体失败（{path}）：{type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            try:
                os.remove(PMING_TTF + ".tmp")
            except OSError:
                pass
            return ""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        main = nb_main()
        super().__init__(*a, directory=(main.root if main else HERE), **kw)

    def translate_path(self, path):
        """URL → 盘上的落点。**不走基类那条**（它只会往一个 directory 里拼）。

        多本时第一段是笔记本的名字（`/工作/会议/图.png`），单本时不带前缀。
        顺手堵了两个一直在的口子：
          · 每一段过 `_seg_blocked`——`.amnote/`、点开头的段、`_编辑备份`、
            `__pycache__` 一律不发（`GET /.amnote/fulltext.db` 原来是能读的）；
          · 落点 realpath 必须还在那一本的根里——库里一条指到家目录的软链接，
            原来照发不误（`/__raw` 那条早就拦了，静态这条没拦）。
        **段判两遍**（跟 `_edit_ok` 同一个道理）：请求里写的那几段判一遍，
        realpath 之后按实际落点再判一遍。只判前者的话，库里一条
        `工作/会议/公开 → ../.amnote` 的软链接就能把 `fulltext.db` 读出去。
        挡下来的返回一个不存在的路径，基类打不开自然回 404，不用另写应答。
        """
        raw = path.split("?", 1)[0].split("#", 1)[0]
        try:
            raw = urllib.parse.unquote(raw, errors="surrogatepass")
        except (UnicodeDecodeError, TypeError):
            return NOWHERE
        segs = []
        for seg in raw.split("/"):
            if not seg or seg == ".":
                continue
            if seg == ".." or "\x00" in seg or _seg_blocked(seg):
                return NOWHERE
            segs.append(seg)
        v = V()
        if nb_multi():
            if not segs:
                return NOWHERE                    # 多本时根目录列不出东西来
            v = nb_by_name(segs.pop(0))
            if v is None or not v.online:
                return NOWHERE
        if not segs:
            return v.real_root
        full = os.path.realpath(os.path.join(v.root, *segs))
        if not (full == v.real_root or full.startswith(v.real_root + os.sep)):
            return NOWHERE
        if full != v.real_root:
            for seg in os.path.relpath(full, v.real_root).split(os.sep):
                if _seg_blocked(seg):
                    return NOWHERE
        return full

    def log_message(self, *a):
        pass

    def _lang(self):
        """这一趟请求说哪种语言：X-AMN-Lang 头 → lang 查询参数 → zh-Hans。

        页面每个 fetch 都带头（hdrs()）；带不了自定义头的取法（<img src>、
        CSS 里的 url()）退到查询参数。认不出的代号一律当简体。
        **只影响文案**：JSON 字段名、配置键、`状态` 的哨兵值一概不动。
        """
        code = (self.headers.get("X-AMN-Lang") or "").strip()
        if code not in LANGS:
            code = ((self._q().get("lang") or [""])[0] or "").strip()
        return set_lang(code)

    def _body(self, b: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        # 一律不许嗅探。/__avatar 发的是用户自己丢进来的字节，只按前几个魔数
        # 认过类型；浏览器要是自作主张把一张「png」当 html 渲染，那就是同源脚本
        self.send_header("X-Content-Type-Options", "nosniff")
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

    def _send_cached(self, path: str, ctype: str, cache: str) -> bool:
        """按块发一份文件，带自己的缓存头。发不出去（文件不在）返回 False。

        **不走 _body**：那条路把整份读进内存、而且强制 Cache-Control: no-store，
        22 MB 的字体每刷新一次就重下一遍。这里分块发，并且自己写缓存头
        （_cache_sent 一置上，end_headers 就不再补 no-store）。HEAD 只发头。
        """
        try:
            st = os.stat(path)
            f = open(path, "rb")
        except OSError:
            return False
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(st.st_size))
            self.send_header("Cache-Control", cache)
            self._cache_sent = True
            self.end_headers()
            if self._head_only:
                return True
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # 页面中途换了字体／关了标签，浏览器直接把连接掐了。不是错。
            self.close_connection = True
        finally:
            f.close()
        return True

    def _pmingliu(self):
        """把新细明体发出去。本机压根没有（也没装 Office）就 404。"""
        path = pmingliu_file()
        if not path or not self._send_cached(path, "font/ttf",
                                             "private, max-age=86400"):
            self._deny(404, T("本机没有新细明体"))

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

    def _deny(self, status: int, msg):
        """拒掉一个请求。给 JSON 不给 html 错误页——前端一律 res.json() 读结果，
        html 错误页会让它在解析那一步炸掉，看不出是被门禁挡的。

        连接一律断开：POST 被拒时请求体还没读完，keep-alive 复用会把下一个请求
        的报文头读成上一个的正文。
        """
        b = json.dumps(_bad(msg), ensure_ascii=False).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
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
            self._deny(403, T("Host 不对"))
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin not in tuple("http://" + h for h in hosts):
            self._deny(403, T("跨站请求不收"))
            return False
        return True

    def _token(self) -> bool:
        if token_ok(self.headers.get("X-AMN-Token") or ""):
            return True
        self._deny(403, T("口令不对。退出 AM·Note 再打开一次。"))
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
            self._deny(405, T("重扫要用 POST"))
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
        elif route == "/__font/pmingliu":
            self._pmingliu()
        elif route.startswith("/__fonts"):
            self._json(json.dumps(fonts_view(), ensure_ascii=False))
        elif route.startswith("/__status"):
            self._json(json.dumps(status(), ensure_ascii=False))
        elif route.startswith("/__notebooks"):
            self._json(json.dumps(notebooks_view(), ensure_ascii=False))
        elif route.startswith("/__profile"):
            # 免口令，跟下面 /__avatar 一个道理：页面启动时并行拉它，
            # 而头像那张是 <img src>，带不了自定义头
            self._json(json.dumps(profile_view(), ensure_ascii=False))
        elif route.startswith("/__avatar"):
            blob, ctype = avatar_bytes()
            if blob is None:
                self._deny(404, ctype)
            else:
                self._body(blob, ctype)          # _body 自带 Cache-Control: no-store
        elif route.startswith("/__agent_setup"):
            self._json(json.dumps(agent_setup_view(), ensure_ascii=False))
        elif route.startswith("/__tree"):
            self._json(json.dumps(tree_view(), ensure_ascii=False))
        elif route.startswith("/__config"):
            # `?nb=名字` 选哪一本的配置，不给就是主笔记本；**名字写错不回落**
            v, err = nb_arg((self._q().get("nb") or [""])[0])
            self._json(json.dumps(_bad(err) if err else read_config(v),
                                  ensure_ascii=False))
        elif route.startswith("/__raw"):
            q = self._q()
            self._json(json.dumps(
                read_raw((q.get("path") or [""])[0],
                         section=(q.get("section") or [""])[0],
                         lines=(q.get("lines") or [""])[0]), ensure_ascii=False))
        elif route.startswith("/__search"):
            self._json(json.dumps(search_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__outline"):
            self._json(json.dumps(outline_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__map"):
            self._json(json.dumps(map_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__links"):
            self._json(json.dumps(links_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__recent"):
            self._json(json.dumps(recent_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__meta"):
            self._json(json.dumps(meta_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__changes"):
            self._json(json.dumps(changes_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__archive"):
            self._json(json.dumps(archive_view(self._q()), ensure_ascii=False))
        elif route.startswith("/__locate"):
            # 免口令：绝对路径 → 对外路径，纯字符串换算（访达双击那条路）
            self._json(json.dumps(locate_view(self._q()), ensure_ascii=False))
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
        self._nosniff_sent = False
        self._head_only = False
        self._ctype = ""
        self._lang()
        bind(None)                               # 每趟从主笔记本起算（keep-alive 复用线程）
        if not self._gate():
            return
        if not self._route():
            super().do_GET()

    def do_HEAD(self):
        self._cache_sent = False
        self._nosniff_sent = False
        self._head_only = True
        self._ctype = ""
        self._lang()
        bind(None)
        if not self._gate():
            return
        if not self._route():
            super().do_HEAD()

    def do_POST(self):
        self._cache_sent = False
        self._nosniff_sent = False
        self._head_only = False
        self._ctype = ""
        self._lang()
        bind(None)
        if not self._gate():
            return
        route = self.path.split("?")[0]
        if route not in ("/__config", "/__reveal", "/__external",
                         "/__save", "/__trash", "/__untrash",
                         "/__state", "/__rescan", "/__extopen",
                         "/__profile", "/__agent_setup", "/__notebooks"):
            self.send_error(404, "not found")
            return
        # **所有 POST 都要口令。** 这是写路由和触发类路由的唯一一道门
        if not self._token():
            return
        # 谁在写：本机的 AI 助手会带这个头（"Claude Code" / "Codex" / …），
        # 用户自己在门户里点的没有。洗一遍再用——它会原样落进流水
        _req.agent = header_name(self.headers.get("X-AMN-Agent"))
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if route == "/__rescan":                 # 只认一个可选的 nb
            body = {}
            if 0 < n <= 1_000_000:
                # body 一定要读干净，不然 keep-alive 会把它当下一个请求的报文头
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = {}
            self._json(json.dumps(
                rescan(body if isinstance(body, dict) else {}), ensure_ascii=False))
            return
        # /__save 传的是整篇正文，上限单独给大一点。/__profile 也要松一档：
        # 头像那 1 MB 是 base64 送来的，撑大 4/3 之后正好卡在 1 MB 那道闸上，
        # 一张合法的头像会被拦成「请求体过大」，报的还不是头像那条话
        cap = (9_000_000 if route == "/__save"
               else 2_000_000 if route == "/__profile" else 1_000_000)
        if n <= 0 or n > cap:
            self._json(json.dumps(_bad(T("请求体为空或过大")), ensure_ascii=False))
            return
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._json(json.dumps(_bad(T("请求不是合法 JSON：{e}", e=e)),
                                  ensure_ascii=False))
            return
        # `?nb=名字` 跟 body 里的 nb 等价：设置面板那两段用查询参数顺手些
        if isinstance(req, dict) and not req.get("nb"):
            nbq = (self._q().get("nb") or [""])[0].strip()
            if nbq:
                req["nb"] = nbq
        try:
            out = {"/__config": write_config, "/__reveal": reveal,
                   "/__external": open_external, "/__extopen": ext_open,
                   "/__save": save_route, "/__state": state_set,
                   "/__trash": trash, "/__untrash": untrash,
                   "/__profile": profile_write,
                   "/__agent_setup": agent_setup_do,
                   "/__notebooks": notebooks_do}[route](req)
        except Exception as e:                       # 界面上要看得见，不能静默 500
            out = {"ok": False, "错误": f"{type(e).__name__}: {e}"}
        # 补一轮全文同步，索引在几秒内就能跟上这次改动。/__trash 和 /__untrash
        # 一样要：文件进出库根，树和搜索里那一条得跟着消失／回来，
        # 而且这一趟同步就是这两条路由记流水的地方（trash / untrash 只记活表）。
        # **记账不在这儿**：save_md / new_md / trash / untrash 在真正动文件之前
        # 就记了，见那四处注释
        if (route in ("/__save", "/__trash", "/__untrash")
                and isinstance(out, dict) and out.get("ok")):
            kick_sync(V())                       # 只补动过的那一本
        self._json(json.dumps(out, ensure_ascii=False))

    def send_header(self, keyword, value):
        # end_headers 按 Content-Type 决定要不要沙箱，先记下来
        low = keyword.lower()
        if low == "content-type":
            self._ctype = str(value)
        elif low == "x-content-type-options":
            self._nosniff_sent = True            # _body 已经发过，别发第二遍
        super().send_header(keyword, value)

    #: 自己能跑脚本的三种文档类型。svg 和 xhtml 顶层打开一样是文档、
    #: 一样能带 <script>，漏掉哪个哪个就成了绕过这道头的口子。
    _SANDBOX_TYPES = ("text/html", "image/svg+xml", "application/xhtml+xml")

    def _is_vault_html(self) -> bool:
        """能跑脚本的文档，响应头上再补一层沙箱。

        隔离本来分两处，各管各的：

        ① 门户里的预览框是 iframe sandbox="allow-scripts"（没给 same-origin），
           文档落进不透明源，读不到门户的 token，fetch 也跨不回服务端；壳那边另有
           一道守卫，didReceiveScriptMessage: 只认主框（isMainFrame），子框喊 amn
           通道不算数。这一层跟本方法没关系。
        ② 地址栏直接打到 /某页.html 是顶层导航，没有 iframe 那一层。在壳里这种导航
           其实到不了——decidePolicy 那边 isShellMainURL 只放 /portal 和 /__* 过，
           主框载入库内文件一律 Cancel（app_shell.m 约 L3296）。所以这个头真正兜的是
           **浏览器模式**：用户拿 Safari / Chrome 开门户，顶层打开一份库内 html 时，
           CSP sandbox 不带 allow-same-origin，文档照样落进不透明源，脚本能跑、
           跨不回门户这边。

        判定只看 Content-Type，不问路径：html / svg / xhtml 这三种都能带 <script>，
        少认一个就是一个口子。/__extasset 回 svg 时也会顺带套上——那条本来就只是把
        一张图显示出来，多这一层没有副作用。/portal 自己就是门户，不套。
        """
        route = self.path.split("?")[0]
        if route in ("/portal", "/portal/"):
            return False
        ctype = (getattr(self, "_ctype", "") or "").lower()
        return ctype.startswith(self._SANDBOX_TYPES)

    def end_headers(self):
        # 会被改写的文档一律不进缓存。后缀表跟 _SANDBOX_TYPES 对齐：库里的 svg 改了，
        # 门户不能还拿旧的那张来显示。
        if not getattr(self, "_cache_sent", False) \
                and self.path.endswith(
                    (".md", ".html", ".htm", ".json", ".svg", ".xhtml")):
            self.send_header("Cache-Control", "no-store")
        if self._is_vault_html():
            self.send_header("Content-Security-Policy", "sandbox allow-scripts")
            if not getattr(self, "_nosniff_sent", False):
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


def _on_term(sig, frame):
    """SIGTERM 抛 SystemExit，让下面的 finally 把口令文件收走。

    壳退出时给的是 SIGTERM，默认处置是当场死——finally 一句都不跑，
    上一趟的 portal.token 就留在盘上了（连不上任何服务，排查时容易看岔）。
    """
    raise SystemExit(0)


if __name__ == "__main__":
    # 口令文件默认 ~/Library/Application Support/AMNote/portal.token，
    # 可以用 --token-file 或环境变量 AMN_TOKEN_FILE 挪走。
    # --root 可以写好几个（5.7：一个进程挂几个笔记本）。
    roots, argv = fulltext.take_root_args(sys.argv[1:])
    if "--token-file" in argv:
        i = argv.index("--token-file")
        if i + 1 < len(argv):
            TOKEN_FILE = argv[i + 1]
    # 个人资料（profile.json / avatar.img）落在哪儿。壳传的就是默认值；
    # 测试时必须传一个 scratch 目录，别写进用户真正在用的那一份
    if "--support-dir" in argv:
        i = argv.index("--support-dir")
        if i + 1 < len(argv):
            SUPPORT_DIR = os.path.abspath(os.path.expanduser(argv[i + 1]))
    # **给了这个才落盘。** 不给就是临时列表：/__notebooks 的增删只在内存里，
    # 开发脚本永远碰不到用户那份 notebooks.json
    if "--notebooks-file" in argv:
        i = argv.index("--notebooks-file")
        if i + 1 < len(argv):
            NOTEBOOKS_FILE = os.path.abspath(os.path.expanduser(argv[i + 1]))
    boot_vaults(roots)
    signal.signal(signal.SIGTERM, _on_term)
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
