#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AM·Note 命令行工具 ＋ MCP 服务。

一句话：让这台 Mac 上的 AI 助手（Claude Code、Codex、别的 agent）和脚本，
用跟门户网页一样的那套接口搜库、读笔记、写笔记。

    amnote map                       先看库地图，知道有哪些目录
    amnote search "报销 流程"         再搜
    amnote read 工作手记/报销.md --section 发票   最后只读要的那一节
    amnote new 今天想到的 <<< "正文"   写一份新的
    amnote mcp                       以 MCP 服务的身份跑（stdio）

设计上的三条：

· **只跟本机门户服务说话。** 所有能力都是 portal_server.py 已有的路由，
  这里不重新实现一遍读写和越权判定——写入范围、备份、流水、冲突检测
  全在服务端一处，命令行绕不过去也不该绕过去。
· **服务没开也能读。** 门户不在跑的时候，只读命令回退到 <库根>/.amnote/
  fulltext.db（上一次的索引），import 同目录的 fulltext 做纯读。
  写命令一律拒绝——写要过服务端那套判定，离线不给。
· **/usr/bin/python3 跑得起来。** Python 3.9，零第三方依赖。

退出码：0 成功 · 1 用法错 · 2 服务不可用 · 3 冲突 · 4 服务端拒绝。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

VERSION = "5.6.0"
HERE = os.path.dirname(os.path.abspath(__file__))

# 发现服务时要扫的端口段。跟 config.json 的默认「端口范围」对齐。
SCAN_FROM, SCAN_TO = 8870, 8900

# MCP 协议版本。客户端报的是这三个里的哪一个就回哪一个，都不认就回最新那个。
MCP_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
MCP_LATEST = "2025-06-18"

READ_CMDS = ("search", "map", "read", "outline", "tree", "recent", "links")
WRITE_CMDS = ("new", "save", "trash")

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 被 MCP 客户端当子进程拉起来时环境里常常没有 LANG，stdout 会退成 ascii，
# 中文一写就 UnicodeEncodeError。三条流都钉死 UTF-8。
for _s in (sys.stdout, sys.stderr, sys.stdin):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


class Fail(Exception):
    """一次带退出码的失败。main 捕住，打到 stderr，按 code 退出。"""

    def __init__(self, msg, code=4):
        Exception.__init__(self, msg)
        self.msg = str(msg)
        self.code = code


class Unreachable(Exception):
    """连不上门户服务。调用方决定是回退离线还是报错。"""


# ── 小工具 ────────────────────────────────────────────────

def _clean(s, limit=0):
    """去控制字符、去首尾空白，可选截断。"""
    s = _CTRL.sub("", str(s or "")).strip()
    return s[:limit] if limit else s


def _when(v):
    """把「改于」格式化成人看的时间，一律到分。库里有两种写法：
    /__tree 里是 epoch 浮点，/__meta、/__search、/__recent 里是
    '%Y-%m-%d %H:%M:%S' 字符串。两种都要认。"""
    if isinstance(v, (int, float)):
        if not v:
            return ""
        return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M")
    return str(v or "")[:16]


def _clamp(v, dflt, lo, hi):
    """数字参数钳位。空的用默认值；读不出数字抛 ValueError，调用方回一条错。"""
    if v in (None, ""):
        return dflt
    return max(lo, min(int(v), hi))


def _out(s=""):
    """人话输出。**统一走这里**：mcp 子命令下 stdout 只许有 JSON-RPC，
    别的地方一句 print 就能把协议流搅坏。"""
    sys.stdout.write(s + "\n")


def _note(s):
    sys.stderr.write(s + "\n")


def _header_value(s):
    """HTTP 头的值只能是 latin-1。名字里有中文时按 UTF-8 的字节发出去
    （服务端拿到后 .encode('latin-1').decode('utf-8') 就是原名）。"""
    try:
        s.encode("latin-1")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8").decode("latin-1")


# ── 目标库、支撑目录、口令 ─────────────────────────────────

def support_dir():
    return os.path.expanduser(
        os.environ.get("AMNOTE_SUPPORT_DIR")
        or "~/Library/Application Support/AMNote")


def cache_dir():
    """读表落在这儿。env `AMNOTE_CACHE_DIR` 能整个挪走（测试时必须挪走）。"""
    return os.path.expanduser(os.environ.get("AMNOTE_CACHE_DIR")
                              or "~/Library/Caches/amnote")


def port_file_path():
    return os.path.expanduser(os.environ.get("AMNOTE_PORT_FILE")
                              or os.path.join(support_dir(), "portal.port"))


def token_file_path():
    return os.path.expanduser(os.environ.get("AMN_TOKEN_FILE")
                              or os.path.join(support_dir(), "portal.token"))


def read_token():
    try:
        with open(token_file_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def defaults_vault():
    """壳把当前库根写在 `defaults` 里。没装 app、没设过就是空。"""
    try:
        p = subprocess.run(["defaults", "read", "app.amnote", "AMNVaultPath"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    if p.returncode != 0:
        return ""
    return p.stdout.decode("utf-8", "replace").strip()


def resolve_vault(arg):
    """目标库：--vault → AMNOTE_VAULT → defaults read app.amnote AMNVaultPath。"""
    for raw in (arg, os.environ.get("AMNOTE_VAULT"), defaults_vault()):
        raw = (raw or "").strip()
        if raw:
            return os.path.abspath(os.path.expanduser(raw))
    return ""


def _same_dir(a, b):
    """两条路径指的是不是同一个文件夹。/tmp 和 /private/tmp 这种要认得出。"""
    if not a or not b:
        return False
    a2 = os.path.abspath(os.path.expanduser(a))
    b2 = os.path.abspath(os.path.expanduser(b))
    if a2 == b2:
        return True
    try:
        return os.path.realpath(a2) == os.path.realpath(b2)
    except OSError:
        return False


# ── 读表：「我上次看到的是哪一版」────────────────────────────
#
# `save` 是整篇覆写，服务端靠请求里的 `基于`（＝你读到的那一刻的「改于」）
# 判有没有冲突。**这个值必须来自「读的那一刻」，不能是「写之前现问一句」**：
# 现问一句拿回来的永远是当前 mtime，`基于` 恒等于现状，冲突检测形同虚设——
# 你读完之后用户在门户里改了三段，你照样一把盖掉。
#
# 所以 read / new / save 各自把看到的「改于」记在这里，save 时取出来用。
# 跨进程也算数（MCP 那条路是另一个进程在 read、又一个在 save），所以落在
# 用户级的缓存目录里，不是内存。
#
# 这是**缓存，不是账本**：删掉它只是下一次 save 会说「先 read 一遍」，
# 没有任何东西丢失。所以读写全程容错，坏了就当空的。

STAMP_FILE = "reads.json"
STAMP_KEEP = 500                      # 记这么多份就够了，超了挤掉最早写进来的


def _stamp_path():
    return os.path.join(cache_dir(), STAMP_FILE)


def _stamp_key(vault, rel):
    """键＝库根的 realpath ＋ tab ＋ 库相对路径。realpath 是为了
    /tmp 和 /private/tmp 这种同一个库两种写法只占一个键。"""
    v = (vault or "").strip()
    if v:
        try:
            v = os.path.realpath(os.path.abspath(os.path.expanduser(v)))
        except OSError:
            v = os.path.abspath(os.path.expanduser(v))
    return "%s\t%s" % (v, rel or "")


def _stamps():
    try:
        with open(_stamp_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def stamp_get(vault, rel):
    v = _stamps().get(_stamp_key(vault, rel))
    return v if isinstance(v, str) else ""


def stamp_put(vault, rel, when):
    """记下「这一份我看到的是这个时间」。写不进去就算了，不打扰用户。"""
    when = str(when or "").strip()
    if not when or not rel:
        return
    d = _stamps()
    key = _stamp_key(vault, rel)
    d.pop(key, None)                              # 重记一遍要排到队尾
    d[key] = when
    while len(d) > STAMP_KEEP:
        d.pop(next(iter(d)))
    path = _stamp_path()
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def agent_name(arg):
    """写请求的署名：--agent → AMNOTE_AGENT → 认得出 Claude Code 就写它，否则 CLI。"""
    for raw in (arg, os.environ.get("AMNOTE_AGENT")):
        raw = _clean(raw, 40)
        if raw:
            return raw
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE"):
        return "Claude Code"
    return "CLI"


# ── 在线：跟门户服务说话 ───────────────────────────────────

class Online(object):
    """一个跑着的 portal_server。所有能力都是它的路由。"""

    kind = "online"

    def __init__(self, port, root, agent="CLI"):
        self.port = int(port)
        self.root = root
        self.agent = agent
        self.base = "http://127.0.0.1:%d" % self.port
        self.last_raw = ""

    # -- 底层 --------------------------------------------------

    def _call(self, route, params=None, payload=None, timeout=30):
        url = self.base + route
        if params:
            clean = [(k, v) for k, v in params.items()
                     if v is not None and v != ""]
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            tok = read_token()
            if not tok:
                raise Fail("找不到口令文件（%s）。AM·Note 正开着吗？"
                           % token_file_path(), 2)
            headers["X-AMN-Token"] = tok
            headers["X-AMN-Agent"] = _header_value(_clean(self.agent, 40))
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data else "GET")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            body = resp.read()
            resp.close()
        except urllib.error.HTTPError as e:
            body = e.read()
        except Exception:                     # URLError / socket / http.client
            raise Unreachable(url)
        text = body.decode("utf-8", "replace")
        self.last_raw = text
        try:
            return json.loads(text)
        except ValueError:
            raise Fail("服务返回的不是 JSON：%s" % text[:200], 4)

    def get(self, route, params=None):
        return self._call(route, params=params)

    def post(self, route, payload):
        return self._call(route, payload=payload)

    # -- 路由 --------------------------------------------------

    def status(self):
        return self.get("/__status")

    def search(self, q, n=None, offset=None, dir=None, type=None,
               since=None, sort=None):
        return self.get("/__search", {"q": q, "n": n, "offset": offset,
                                      "dir": dir, "type": type,
                                      "since": since, "sort": sort})

    def map(self, dir=None, max=None, depth=None, budget=None):
        return self.get("/__map", {"dir": dir, "max": max,
                                   "depth": depth, "budget": budget})

    def read(self, path, section=None, lines=None):
        return self.get("/__raw", {"path": path, "section": section,
                                   "lines": lines})

    def outline(self, path):
        return self.get("/__outline", {"path": path})

    def tree(self):
        return self.get("/__tree")

    def recent(self, days=None, n=None, type=None):
        return self.get("/__recent", {"days": days, "n": n, "type": type})

    def links(self, path):
        return self.get("/__links", {"path": path})

    def meta(self, path):
        return self.get("/__meta", {"path": path})

    def save(self, payload):
        return self.post("/__save", payload)

    def trash(self, path):
        return self.post("/__trash", {"路径": path})

    def agent_setup(self, action=None):
        if action:
            return self.post("/__agent_setup", {"动作": action})
        return self.get("/__agent_setup")


def _probe(port, timeout=1.5):
    """这个端口上是不是一个门户服务？是就返回它的 /__status。"""
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/__status" % port,
                                     headers={"Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read(200000)
        resp.close()
    except Exception:
        return None
    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("库根") else None


def discover(vault, agent="CLI"):
    """找到那个服务着目标库的门户。顺序：AMNOTE_PORT → 端口文件 → 扫 8870–8900。

    **端口对上不等于库对上。** 一台机器上可以同时开着两个库（用户自己那份 ＋
    一份测试库），随手挑一个「有门户在应答」的端口，`amnote save` 就会把笔记
    写进另一个人的笔记库。所以：

    · `AMNOTE_PORT` 是明写的指定，认它给的那个——但**探不到就当场报错**，
      不许悄悄往下走去找别的服务：明写了端口还回退，等于把「我要这一个」
      当成了「随便哪个都行」。
    · 端口文件是 AM·Note 自己写的「当前这一份」。目标库不知道时认它，
      知道就必须核上 `库根`。
    · 扫端口是最后的兜底，**只在目标库已知时用**，而且必须核上库根：
      不知道要哪个库的时候，扫出来的那一个只是「这台机器上碰巧开着的某个库」。
    """
    env_port = (os.environ.get("AMNOTE_PORT") or "").strip()
    if env_port:
        try:
            p = int(env_port)
        except ValueError:
            raise Fail("AMNOTE_PORT 不是数字：%s" % env_port, 1)
        st = _probe(p, timeout=3.0)
        if not st:
            raise Fail("AMNOTE_PORT=%s 上没有 AM·Note 服务" % env_port, 2)
        if vault and not _same_dir(st.get("库根"), vault):
            raise Fail("AMNOTE_PORT=%s 上那个服务开的是别的库（%s）"
                       % (env_port, st.get("库根") or "?"), 2)
        return Online(p, st.get("库根") or vault, agent)

    try:
        with open(port_file_path(), encoding="utf-8") as f:
            p = int((f.read() or "").strip())
    except (OSError, ValueError):
        p = 0
    if p:
        st = _probe(p, timeout=3.0)
        if st and (not vault or _same_dir(st.get("库根"), vault)):
            return Online(p, st.get("库根") or vault, agent)

    if vault:
        for p in range(SCAN_FROM, SCAN_TO + 1):
            st = _probe(p, timeout=1.0)
            if st and _same_dir(st.get("库根"), vault):
                return Online(p, st.get("库根") or vault, agent)
    return None


# ── 离线：直接读上一次的索引 ───────────────────────────────
#
# 门户没开时走这条。**能借 fulltext 的就借**：搜索的筛选、加权分、片段的
# 行/小节、md 标题口径、按标题／行号切一段、库地图的排版，5.6 起全是
# fulltext 里的公共函数，服务端那条路走的也是它们——在这儿重写一遍，
# `amnote map` 就会随「AM·Note 开没开」印出两份不一样的地图，
# 而 Agent 正是靠这份地图导航的。
#
# **一个字节都不往库里写。** fulltext.configure(readonly=True) 之后不建
# `.amnote/`、不建 `backups/`，connect() 走 `mode=ro` 的 URI、不设
# journal_mode——不然一条 `amnote search` 会在别人的笔记文件夹里留下一个
# 目录和两个 `-wal` / `-shm` 文件。


class Offline(object):
    """门户没开时的只读通道。数据源是 <库根>/.amnote/fulltext.db。

    **只读。** 不同步、不建库、不写任何东西——正文、大纲、小节全从磁盘现读，
    命中和排序走 fulltext.search。索引是上一次 AM·Note 开着时留下的，
    可能比磁盘旧一点（新加的文件还没进去），所以每条命令都在 stderr 说一句。
    """

    kind = "offline"

    def __init__(self, vault):
        self.root = vault
        self.last_raw = ""
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        try:
            import fulltext
        except Exception as e:
            raise Fail("离线模式要用到同目录的 fulltext.py：%s" % e, 2)
        db = os.path.join(vault or "", ".amnote", "fulltext.db")
        if not os.path.isfile(db):
            raise Fail("AM·Note 没在运行，%s 里也没有上次的索引。打开 AM·Note 再试。"
                       % os.path.join(vault or "", ".amnote"), 2)
        try:
            fulltext.configure(root=vault, readonly=True)
        except SystemExit:
            raise Fail("这个文件夹打不开：%s" % vault, 2)
        self.ft = fulltext

    # -- 数据库 ------------------------------------------------

    def _rows(self, where="", args=()):
        con = self.ft.connect()
        try:
            sql = "SELECT 路径,类型,mtime,大小,正文,备注 FROM 文档"
            if where:
                sql += " WHERE " + where
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def _paths(self):
        """库里有哪些路径。**只取这一列**：出链要判「这份在不在」，
        把全库正文拉出来做这件事是几十 MB 的白读。"""
        con = self.ft.connect()
        try:
            return {r[0] for r in con.execute("SELECT 路径 FROM 文档")}
        finally:
            con.close()

    def _bodies(self, rels):
        """一批路径 → {路径: (类型, 正文)}。搜索结果补标题用，一次查完，
        不是一条一句 SELECT。"""
        rels = [r for r in dict.fromkeys(rels) if r]
        if not rels:
            return {}
        out = {}
        con = self.ft.connect()
        try:
            for i in range(0, len(rels), 400):       # SQLite 的参数上限
                bit = rels[i:i + 400]
                q = ("SELECT 路径,类型,正文 FROM 文档 WHERE 路径 IN (%s)"
                     % ",".join("?" * len(bit)))
                for rel, kind, body in con.execute(q, bit):
                    out[rel] = (kind, body or "")
        finally:
            con.close()
        return out

    def _one(self, rel):
        rows = self._rows("路径=?", (rel,))
        return rows[0] if rows else None

    def _full(self, rel):
        return os.path.join(self.root, rel.replace("/", os.sep))

    def _disk(self, rel):
        """正文从磁盘现读（索引里那份可能是超长截断过的）。"""
        full = self._full(rel)
        if not os.path.isfile(full):
            return None
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def _title(self, rel, kind, body):
        """跟 portal_server.title_of 一个口径的简版（少一层 disambiguate）：
        md 取第一个 `# `，html 取抽取正文的第一行，都没有就退回文件名主干。
        三个函数都是 fulltext 里那一份，服务端用的也是它们。"""
        if kind == "md":
            t = self.ft.first_heading(body or "")
        elif kind == "html":
            t = self.ft.html_title(body or "")
        else:
            t = ""
        return t or self.ft.clean_title(os.path.basename(rel))

    # -- 路由的离线版本 ----------------------------------------

    def status(self):
        st = self.ft.index_status()
        return {"ok": True, "状态": "离线", "端口": 0, "库根": self.root,
                "上次扫描": st.get("上次同步", ""),
                "索引": {"收录": st.get("收录", 0), "状态": st.get("状态", "")},
                "门禁": True, "随手记目录": "随手记", "离线": True}

    def search(self, q, n=None, offset=None, dir=None, type=None,
               since=None, sort=None):
        n = 200 if n in (None, "") else max(1, min(int(n), 500))
        offset = 0 if offset in (None, "") else max(0, int(offset))
        types = [t.strip() for t in str(type or "").split(",") if t.strip()]
        try:
            r = self.ft.search(q or "", limit=n, offset=offset,
                               subdir=(dir or "").strip().strip("/"),
                               types=types, since=since, sort=sort or "score")
        except ValueError:                       # since= 读不懂，跟服务端一个话
            return _err("since 要写成天数（7）或日期（2026-09-01）", "bad_type")
        # 服务端的 search_view 从树上补「标题」，这里从索引里的正文补
        hits = r.get("结果") or []
        got = self._bodies([h.get("路径") or "" for h in hits])
        for h in hits:
            rel = h.get("路径") or ""
            kind, body = got.get(rel, (h.get("类型") or "", ""))
            h["标题"] = self._title(rel, kind or (h.get("类型") or ""), body)
        return r

    def map(self, dir=None, max=None, depth=None, budget=None):
        try:
            per = _clamp(max, self.ft.MAP_MAX, 1, self.ft.MAP_MAX_CAP)
            deep = _clamp(depth, 0, 0, self.ft.MAP_DEPTH_CAP)
            room = _clamp(budget, self.ft.MAP_BUDGET, 1000,
                          self.ft.MAP_BUDGET_CAP)
        except (TypeError, ValueError):
            return _err("map 的参数要是数字", "bad_type")
        con = self.ft.connect()
        try:
            rows = self.ft.map_rows(con)
        finally:
            con.close()
        name = os.path.basename(self.root.rstrip(os.sep)) or self.root
        return self.ft.map_text(rows, name, sub=dir or "", per=per,
                                depth=deep, budget=room)

    def read(self, path, section=None, lines=None):
        if not path.endswith(".md"):
            return _err("只有 md 能读源码", "bad_type")
        text = self._disk(path)
        if text is None:
            return _err("找不到这份：%s" % path, "gone")
        cut, err = self.ft.text_slice(text, section=section or "",
                                      lines=lines or "")
        if err:
            return _err(SLICE_ERR[err] % {"s": section or "",
                                          "p": path}, SLICE_CODE[err])
        st = os.stat(self._full(path))
        return {"ok": True, "路径": path, "正文": cut["正文"],
                "字节": st.st_size,
                "改于": datetime.fromtimestamp(
                    st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "行起": cut["行起"], "行止": cut["行止"], "行数": cut["行数"]}

    def outline(self, path):
        low = path.lower()
        if not low.endswith((".md", ".html", ".htm")):
            return _err("只有 md 和 html 有大纲", "bad_type")
        text = self._disk(path)
        if text is None:
            return _err("找不到这份：%s" % path, "gone")
        kind = "md" if low.endswith(".md") else "html"
        row = self._one(path)
        heads = [{"级": h["级"], "文本": h["文本"], "行": h["行"]}
                 for h in self.ft.md_headings(text)] if kind == "md" else []
        head_body = text if kind == "md" else ((row[4] if row else "") or "")
        st = os.stat(self._full(path))
        return {"ok": True, "路径": path,
                "标题": self._title(path, kind, head_body),
                "大纲": heads,
                "行数": len(self.ft.lines_of(text)), "字数": len(text),
                "改于": datetime.fromtimestamp(
                    st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")}

    def tree(self):
        docs = []
        for rel, kind, mt, size, body, _n in self._rows():
            if kind not in ("md", "html"):
                continue
            docs.append({"路径": rel, "标题": self._title(rel, kind, body or ""),
                         "类型": kind, "改于": round(mt or 0, 1),
                         "预览": self.ft.list_preview(body or "", kind)})
        docs.sort(key=lambda x: -x["改于"])
        dirs = sorted({r["路径"].split("/")[0] for r in docs
                       if "/" in r["路径"]})
        return {"ok": True, "目录": [{"名称": d, "显示名": d, "份数": 0,
                                      "子目录": []} for d in dirs],
                "文档": docs, "附件": [], "随手记": [],
                "根文档": sum(1 for r in docs if "/" not in r["路径"]),
                "总数": len(docs), "生成时间": ""}

    def recent(self, days=None, n=None, type=None):
        days = 7 if days in (None, "") else max(1, min(int(days), 365))
        n = 50 if n in (None, "") else max(1, min(int(n), 500))
        kinds = sorted({t.strip().lower() for t in str(type or "").split(",")
                        if t.strip()})
        cut = time.time() - days * 86400
        where, args = ["mtime>=?"], [cut]
        if kinds:
            where.append("lower(类型) IN (%s)" % ",".join("?" * len(kinds)))
            args += kinds
        rows = self._rows(" AND ".join(where) + " ORDER BY mtime DESC LIMIT ?",
                          tuple(args) + (n,))
        out = []
        for rel, kind, mt, size, body, _note in rows:
            out.append({"路径": rel, "标题": self._title(rel, kind, body or ""),
                        "类型": kind,
                        "改于": datetime.fromtimestamp(
                            mt or 0).strftime("%Y-%m-%d %H:%M:%S"),
                        "大小": size or 0})
        return {"ok": True, "文档": out}

    def links(self, path):
        row = self._one(path)
        body = self._disk(path)
        if row is None and body is None:
            return _err("找不到这份：%s" % path, "gone")
        text = body if body is not None else (row[4] or "")
        # 出链跟服务端一个口径：路径链接看文件在不在，[[题名]] 看库里有没有
        # 同名的一篇（文件名主干或标题对上）。**题名那批也要给出来**——
        # 少给的话，同一条 `amnote links` 在 AM·Note 开着和没开着时条数不一样
        names, stems = set(), set()
        for rel2, kind2, _mt, _sz, body2, _n in self._rows():
            stems.add(os.path.splitext(rel2.rsplit("/", 1)[-1])[0].lower())
            names.add(self._title(rel2, kind2, body2 or "").strip().lower())
        names.discard("")
        have = self._paths()
        out_links = []
        for kind, tgt, txt in self.ft.extract_links(path, text):
            if kind == "路径":
                live = tgt in have or os.path.isfile(self._full(tgt))
            else:
                n = (tgt or "").strip().lower()
                live = bool(n) and (n in stems or n in names)
            out_links.append({"目标": tgt, "文本": txt or "",
                              "存在": bool(live), "类型": kind})
        kind0 = row[1] if row else "md"
        title = self._title(path, kind0, text)
        stem = os.path.splitext(os.path.basename(path))[0]
        back = [{"源": s, "文本": ""}
                for s in self.ft.backlinks(path, title, stem)]
        return {"ok": True, "路径": path, "出链": out_links, "反链": back}

    # -- 写：一律拒 -------------------------------------------

    def _refuse(self, *a, **k):
        raise Fail("AM·Note 没在运行，写笔记要先打开它。", 2)

    meta = _refuse
    save = _refuse
    trash = _refuse
    agent_setup = _refuse


# fulltext.text_slice 的错误代号 → 一句话 ＋ 跟服务端同名的短码
SLICE_ERR = {"bad_lines": "lines 要写成 A-B",
             "out_of_range": "行号超出这份的范围",
             "no_section": "这份里没有这一节：%(s)s"}
SLICE_CODE = {"bad_lines": "bad_type", "out_of_range": "gone",
              "no_section": "gone"}


def _err(msg, code=""):
    d = {"ok": False, "错误": msg}
    if code:
        d["代码"] = code
    return d



# ── 人话输出 ──────────────────────────────────────────────

def render_status(d):
    _out("状态：%s" % (d.get("状态") or "?"))
    if d.get("名字"):
        _out("名字：%s" % d["名字"])
    _out("库根：%s" % (d.get("库根") or ""))
    if d.get("端口"):
        _out("端口：%d" % d["端口"])
    idx = d.get("索引") or {}
    _out("索引：收录 %s 份（%s）" % (idx.get("收录", 0), idx.get("状态") or ""))
    if d.get("上次扫描"):
        _out("上次扫描：%s" % d["上次扫描"])
    if d.get("随手记目录"):
        _out("随手记目录：%s" % d["随手记目录"])


def render_snippet(f):
    bits = []
    if f.get("行"):
        bits.append("L%s" % f["行"])
    if f.get("小节"):
        bits.append("[%s]" % f["小节"])
    head = (" ".join(bits) + " ") if bits else ""
    return "    %s%s【%s】%s" % (head, f.get("前") or "", f.get("中") or "",
                                f.get("后") or "")


def render_search(d):
    hits = d.get("结果") or []
    total = d.get("总命中", len(hits))
    off = d.get("偏移") or 0
    if not hits:
        _out("没有命中。")
        return
    _out("命中 %s 份，这里列第 %d–%d 条。" % (total, off + 1, off + len(hits)))
    for h in hits:
        line = " · ".join([x for x in (h.get("路径"), h.get("标题"),
                                       _when(h.get("改于"))) if x])
        _out("")
        _out(line)
        for f in (h.get("片段") or []):
            _out(render_snippet(f))


def render_outline(d):
    _out("%s · %s" % (d.get("路径") or "", d.get("标题") or ""))
    _out("%s 行 · %s 字 · 改于 %s" % (d.get("行数", 0), d.get("字数", 0),
                                      _when(d.get("改于"))))
    for h in (d.get("大纲") or []):
        _out("%s%s  (L%s)" % ("  " * (int(h.get("级", 1)) - 1),
                              h.get("文本") or "", h.get("行", "")))
    if not (d.get("大纲") or []):
        _out("（没有标题）")


def render_tree(d):
    docs = d.get("文档") or []
    dirs = d.get("目录") or []
    _out("共 %s 篇（根目录 %s 篇，%d 个目录）"
         % (d.get("总数", len(docs)), d.get("根文档", 0), len(dirs)))
    for r in docs:
        tag = ("✦ %s · " % r["代理"]) if r.get("代理") else ""
        _out("%s · %s · %s%s" % (r.get("路径") or "", r.get("标题") or "",
                                 tag, _when(r.get("改于"))))


def render_recent(d):
    docs = d.get("文档") or []
    if not docs:
        _out("这段时间没有改动。")
        return
    for r in docs:
        tag = ("✦ %s · " % r["代理"]) if r.get("代理") else ""
        _out("%s · %s · %s%s" % (_when(r.get("改于")), r.get("路径") or "",
                                 tag, r.get("标题") or ""))


def render_links(d):
    out_l = d.get("出链") or []
    back = d.get("反链") or []
    _out("出链 %d 条" % len(out_l))
    for l in out_l:
        _out("  → %s%s" % (l.get("目标") or "",
                           "" if l.get("存在") else "（不存在）"))
    _out("反链 %d 条" % len(back))
    for l in back:
        _out("  ← %s" % (l.get("源") or ""))


# ── 子命令 ────────────────────────────────────────────────

def _clash(d):
    """这条响应是不是「编辑期间被别处改过」。服务端给 代码: conflict；
    门户那条老路只给 需确认，两个都认。"""
    return bool(d.get("代码") == "conflict" or d.get("需确认"))


def need_ok(d):
    """服务端说 ok=False 就停在这儿，退出码 4。"""
    if not isinstance(d, dict):
        raise Fail("服务返回的形状不对", 4)
    if d.get("ok"):
        return d
    raise Fail(d.get("错误") or "服务端拒绝了这次请求", 4)


def emit_json(src, d, conflict=False):
    """--json 的出口。**JSON 照出，退出码照给**：脚本要的是「拿到原样的响应」
    ＋「知道成没成」，只给其中一样都不够用。"""
    dump(src, d)
    if d.get("ok"):
        return
    code = 3 if (conflict and _clash(d)) else 4
    raise Fail(d.get("错误") or "服务端拒绝了这次请求", code)


def body_from(args):
    """正文：--body-file 优先，否则读标准输入。"""
    if getattr(args, "body_file", None):
        try:
            with open(args.body_file, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            raise Fail("读不了 %s：%s" % (args.body_file, e), 1)
    if sys.stdin.isatty():
        _note("从标准输入读正文，输完按 Ctrl-D。")
    try:
        return sys.stdin.read()
    except KeyboardInterrupt:
        raise Fail("没有正文", 1)


def cmd_status(src, args):
    d = src.status()
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    render_status(d)


def cmd_search(src, args):
    d = src.search(args.query, n=args.n, offset=args.offset, dir=args.dir,
                   type=args.type, since=args.since, sort=args.sort)
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    render_search(d)


def cmd_map(src, args):
    d = src.map(dir=args.dir, max=args.max, depth=args.depth)
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    sys.stdout.write(d.get("地图") or "")
    if d.get("截断"):
        _note("地图超出预算，已截断。用 --dir 展开某个目录。")


def cmd_read(src, args):
    if args.section and args.lines:
        _note("同时给了 --section 和 --lines，按 --lines 来。")
    d = src.read(args.path, section=None if args.lines else args.section,
                 lines=args.lines)
    # 读到哪一版就记哪一版，接下来的 save 靠它填「基于」
    if isinstance(d, dict) and d.get("ok"):
        stamp_put(src.root, d.get("路径") or args.path, d.get("改于"))
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    sys.stdout.write(d.get("正文") or "")
    if not (d.get("正文") or "").endswith("\n"):
        sys.stdout.write("\n")


def cmd_outline(src, args):
    d = src.outline(args.path)
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    render_outline(d)


def cmd_tree(src, args):
    d = src.tree()
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    render_tree(d)


def cmd_recent(src, args):
    d = src.recent(days=args.days, n=args.n, type=args.type)
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    render_recent(d)


def cmd_links(src, args):
    d = src.links(args.path)
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    render_links(d)


def _new_path(src, title, dir_arg):
    title = _clean(title, 120)
    if not title:
        raise Fail("标题不能是空的", 1)
    if "/" in title:
        raise Fail("标题里不能有斜杠（要放进某个目录用 --dir）", 1)
    if not title.endswith(".md"):
        title += ".md"
    if dir_arg is None:
        st = src.status()
        need_ok(st)
        d = (st.get("随手记目录") or "随手记").strip("/")
    else:
        d = dir_arg.strip().strip("/")
    return (d + "/" + title) if d and d != "." else title


def cmd_new(src, args):
    body = body_from(args)
    if not body.strip():
        raise Fail("正文不能是空的", 1)
    rel = _new_path(src, args.title, args.dir)
    d = src.save({"新建": True, "路径": rel, "正文": body})
    if isinstance(d, dict) and d.get("ok"):
        stamp_put(src.root, d.get("路径") or rel, d.get("改于"))
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    _out(d.get("路径") or rel)


NO_STAMP = ("这份还没读过，「基于」就没有可填的。"
            "先 amnote read 这份，或加 --based/--force。")
NO_STAMP_MCP = ("这份还没读过，「基于」就没有可填的。"
                "先用 read_note 读一遍，或者给 based / force。")


def save_based(src, path, based_arg, force):
    """这一次 save 拿什么当「基于」。顺序：--based → 读表 → 没有就停下。

    **停下，不是空着往下走。** 「基于」缺席时服务端会照写，等于把冲突检测
    关掉；而「没读过就整篇覆写」本身就该拦——不知道原来写的是什么，
    这一次覆写只可能是把别人的内容抹掉。
    """
    if force:
        return ""
    got = _clean(based_arg)
    if got:
        return got
    got = stamp_get(src.root, path)
    if got:
        return got
    raise Fail(NO_STAMP, 1)


def cmd_save(src, args):
    body = body_from(args)
    try:
        based = save_based(src, args.path, getattr(args, "based", None),
                           args.force)
    except Fail as e:
        # --json 的约定是「拿得到原样的响应 ＋ 知道成没成」。这一条是我们自己
        # 拦下的，服务端那边没有响应可转，就地拼一份形状一样的
        if args.json:
            _out(json.dumps(_err(e.msg, "bad_type"), ensure_ascii=False))
        raise
    payload = {"路径": args.path, "正文": body}
    if based:
        payload["基于"] = based
    if args.force:
        payload["强制"] = True
    d = src.save(payload)
    if isinstance(d, dict) and d.get("ok"):      # 写成功了，读表跟到新的那一版
        stamp_put(src.root, d.get("路径") or args.path, d.get("改于"))
    if args.json:
        return emit_json(src, d, conflict=True)
    if not d.get("ok") and _clash(d):
        _note(str(d.get("错误") or "这份在别处被改过了。"))
        raise Fail("要盖掉那次改动就加 --force。", 3)
    need_ok(d)
    _out("%s：%s" % (d.get("路径") or args.path, d.get("结果") or "已保存"))


def cmd_trash(src, args):
    d = src.trash(args.path)
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    _out("已挪进废纸篓：%s" % (d.get("路径") or args.path))


def cmd_install_skill(src, args):
    d = src.agent_setup("skill")
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    _out(d.get("结果") or "已安装")
    _out((d.get("skill") or {}).get("路径") or "")


def cmd_agents_md(src, args):
    d = src.agent_setup("agents_md" if args.write else None)
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    info = d.get("agents_md") or {}
    if args.write:
        _out(d.get("结果") or "已写入")
    _out("%s%s" % (info.get("路径") or "",
                   "（已存在）" if info.get("已存在") else "（还没有）"))
    if not args.write:
        _out("加 --write 在库根生成它。")


def cmd_mcp_config(src, args):
    d = src.agent_setup()
    if args.json:
        return emit_json(src, d)
    need_ok(d)
    if args.client == "codex":
        sys.stdout.write(d.get("codex配置") or "")
    else:
        _out(d.get("mcp命令") or "")


def dump(src, d):
    """--json：在线原样把服务端那串打出来，离线打自己拼的这份。"""
    raw = getattr(src, "last_raw", "")
    if raw:
        sys.stdout.write(raw if raw.endswith("\n") else raw + "\n")
    else:
        _out(json.dumps(d, ensure_ascii=False))


# ── MCP（stdio，换行分隔的 JSON-RPC 2.0） ────────────────────

TOOLS = [
    {"name": "search_notes",
     "description": "在笔记库里全文搜索，返回命中的路径、标题和带行号的片段。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "query": {"type": "string",
                       "description": "关键词，空格分开就是 AND。"},
             "limit": {"type": "integer", "default": 10,
                       "description": "最多返回几条，默认 10。"},
             "dir": {"type": "string", "description": "只搜这个库相对目录。"},
             "type": {"type": "string",
                      "description": "只搜这些类型，逗号分开：md,html,pdf,xlsx,csv。"},
             "since": {"type": "string",
                       "description": "最近 N 天（写数字）或某天起（2026-09-01）。"},
         },
         "required": ["query"]}},
    {"name": "note_map",
     "description": "笔记库地图：目录结构 ＋ 每篇的标题和一句话。先看它再搜。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "dir": {"type": "string", "description": "只画这个目录。"},
             "max_per_folder": {"type": "integer", "default": 20,
                                "description": "每个目录最多列几篇，默认 20。"},
         },
         "required": []}},
    {"name": "read_note",
     "description": "读一份笔记的源码。可以只读某一节或某几行。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "path": {"type": "string", "description": "库相对路径，如 笔记/周末计划.md。"},
             "section": {"type": "string", "description": "只读这个标题下的一节。"},
             "lines": {"type": "string", "description": "只读这几行，写成 A-B。"},
         },
         "required": ["path"]}},
    {"name": "note_outline",
     "description": "一份笔记的标题大纲（级别、文本、行号）和字数。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "path": {"type": "string", "description": "库相对路径。"},
         },
         "required": ["path"]}},
    {"name": "recent_notes",
     "description": "最近改动过的笔记，按时间倒序。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "days": {"type": "integer", "default": 7,
                      "description": "往回看几天，默认 7。"},
             "limit": {"type": "integer", "default": 30,
                       "description": "最多返回几条，默认 30。"},
         },
         "required": []}},
    {"name": "note_links",
     "description": "一份笔记的出链和反链（谁引用了它）。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "path": {"type": "string", "description": "库相对路径。"},
         },
         "required": ["path"]}},
    {"name": "create_note",
     "description": "新建一份笔记。不覆盖已有的文件。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "title": {"type": "string", "description": "标题，会当成文件名。"},
             "body": {"type": "string", "description": "Markdown 正文。"},
             "dir": {"type": "string",
                     "description": "放进哪个目录，不给就放随手记目录。"},
         },
         "required": ["title", "body"]}},
    {"name": "save_note",
     "description": "整篇覆写一份已有的笔记。**先 read_note 拿到全文**，"
                    "改完整篇写回；没读过就写会被拒。别人在这期间改过也会拒绝，"
                    "除非 force。",
     "inputSchema": {
         "type": "object",
         "properties": {
             "path": {"type": "string", "description": "库相对路径。"},
             "body": {"type": "string", "description": "新的整篇正文。"},
             "based": {"type": "string",
                       "description": "你读到的那一版的「改于」；"
                                      "不给就用上次 read_note 记下的那个。"},
             "force": {"type": "boolean", "default": False,
                       "description": "冲突时也照写，默认 false。"},
         },
         "required": ["path", "body"]}},
]


def _mcp_text(text, is_error=False):
    out = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["isError"] = True
    return out


def _render_to_string(fn, d):
    """把人话渲染函数的输出收进字符串，给 MCP 用。"""
    buf = []
    real = sys.stdout
    class _Cap(object):
        def write(self, s):
            buf.append(s)
        def flush(self):
            pass
    sys.stdout = _Cap()
    try:
        fn(d)
    finally:
        sys.stdout = real
    return "".join(buf)


def mcp_call(getsrc, name, args):
    """跑一个工具。返回 tools/call 的 result。"""
    args = args if isinstance(args, dict) else {}
    try:
        src = getsrc()
        if name == "search_notes":
            d = need_ok(src.search(args.get("query") or "",
                                   n=args.get("limit") or 10,
                                   dir=args.get("dir"), type=args.get("type"),
                                   since=args.get("since")))
            return _mcp_text(_render_to_string(render_search, d))
        if name == "note_map":
            d = need_ok(src.map(dir=args.get("dir"),
                                max=args.get("max_per_folder")))
            return _mcp_text(d.get("地图") or "")
        if name == "read_note":
            path = args.get("path") or ""
            d = need_ok(src.read(path, section=args.get("section"),
                                 lines=args.get("lines")))
            # 跟命令行同一张读表（同一个文件），所以 read 和 save 分在
            # 两个进程里也接得上
            stamp_put(src.root, d.get("路径") or path, d.get("改于"))
            return _mcp_text(d.get("正文") or "")
        if name == "note_outline":
            d = need_ok(src.outline(args.get("path") or ""))
            return _mcp_text(_render_to_string(render_outline, d))
        if name == "recent_notes":
            d = need_ok(src.recent(days=args.get("days") or 7,
                                   n=args.get("limit") or 30))
            return _mcp_text(_render_to_string(render_recent, d))
        if name == "note_links":
            d = need_ok(src.links(args.get("path") or ""))
            return _mcp_text(_render_to_string(render_links, d))
        if name == "create_note":
            rel = _new_path(src, args.get("title") or "", args.get("dir"))
            body = args.get("body")
            if not isinstance(body, str) or not body.strip():
                return _mcp_text("正文不能是空的", True)
            d = need_ok(src.save({"新建": True, "路径": rel, "正文": body}))
            stamp_put(src.root, d.get("路径") or rel, d.get("改于"))
            return _mcp_text("已新建：%s" % (d.get("路径") or rel))
        if name == "save_note":
            path = args.get("path") or ""
            body = args.get("body")
            if not isinstance(body, str):
                return _mcp_text("body 得是文本", True)
            payload = {"路径": path, "正文": body}
            if args.get("force"):
                payload["强制"] = True
            else:
                based = _clean(args.get("based")) or stamp_get(src.root, path)
                if not based:
                    return _mcp_text(NO_STAMP_MCP, True)
                payload["基于"] = based
            d = src.save(payload)
            if not d.get("ok"):
                return _mcp_text(str(d.get("错误") or "保存被拒绝"), True)
            stamp_put(src.root, d.get("路径") or path, d.get("改于"))
            return _mcp_text("已保存：%s" % (d.get("路径") or path))
        return None                                    # 不认识这个工具
    except Fail as e:
        return _mcp_text(e.msg, True)
    except Unreachable:
        inv = getattr(getsrc, "invalidate", None)   # 下次重新找一遍服务
        if inv:
            inv()
        return _mcp_text("跟 AM·Note 的连接断了。它还开着吗？", True)
    except Exception as e:
        return _mcp_text("%s: %s" % (type(e).__name__, e), True)


def _mcp_one(getsrc, req):
    """处理一条请求，返回要回的那个对象；通知（没有 id）返回 None。"""
    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    rid = req.get("id")
    method = req.get("method") or ""
    params = req.get("params") if isinstance(req.get("params"), dict) else {}

    def ok(result):
        return None if rid is None else {"jsonrpc": "2.0", "id": rid,
                                         "result": result}

    def bad(code, message):
        return None if rid is None else {"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": code,
                                                   "message": message}}

    if method == "initialize":
        want = str(params.get("protocolVersion") or "")
        return ok({"protocolVersion":
                   want if want in MCP_PROTOCOLS else MCP_LATEST,
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "amnote", "version": VERSION}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        res = mcp_call(getsrc, name, params.get("arguments"))
        if res is None:
            return bad(-32602, "没有这个工具：%s" % name)
        return ok(res)
    if method.startswith("notifications/"):
        return None                                # 通知，一律不回
    return bad(-32601, "Method not found: %s" % method)


def run_mcp(getsrc):
    """stdio 上的 MCP 服务。一行一个 JSON，stdout 只许有它们。

    一行也可以是一个**数组**（JSON-RPC 2.0 的批量请求）：逐条处理，
    回一个数组，通知不占位；整批都是通知就一个字都不回。客户端把
    `initialize` 和 `notifications/initialized` 打成一包发过来是常见做法，
    原来那条路直接 `continue` 掉整包，握手就卡在那儿了。
    """
    _note("amnote mcp %s：等 stdin 上的 JSON-RPC。" % VERSION)
    out = sys.stdout

    def send(obj):
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    while True:
        line = sys.stdin.readline()
        if not line:                               # stdin 关了＝客户端走了
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(req, list):
            if not req:                            # 空数组：协议说回一条 Invalid Request
                send({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32600, "message": "Invalid Request"}})
                continue
            batch = [r for r in (_mcp_one(getsrc, one) for one in req)
                     if r is not None]
            if batch:
                send(batch)
            continue
        res = _mcp_one(getsrc, req)
        if res is not None:
            send(res)


# ── 参数 ──────────────────────────────────────────────────

class Parser(argparse.ArgumentParser):
    """只改一件事：用法错退 1，不退 argparse 默认的 2。

    2 在这套工具里是「AM·Note 没在运行」，脚本靠它决定要不要提示用户开 app；
    参数打错也退 2 的话，两种完全不同的情况分不开。
    add_subparsers 默认拿 type(self) 当子解析器的类，所以子命令自动跟着。
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        _note("%s：%s" % (self.prog, message))
        sys.exit(1)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", metavar="路径", default=argparse.SUPPRESS,
                        help="笔记库文件夹。不给就用 AMNOTE_VAULT，"
                             "再不给就问 AM·Note 自己记的那个。")
    common.add_argument("--agent", metavar="名字", default=argparse.SUPPRESS,
                        help="写笔记时的署名，会记进变更流水。")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="原样输出服务端的 JSON。")

    p = Parser(
        prog="amnote", parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="AM·Note 命令行：搜库、读笔记、写笔记，也能当 MCP 服务跑。",
        epilog="退出码：0 成功 · 1 用法错 · 2 服务不可用 · 3 冲突 · 4 服务端拒绝。\n"
               "AM·Note 没开着时，只读的那几条会用上一次的索引。")
    p.add_argument("--version", action="version", version="amnote " + VERSION)
    sub = p.add_subparsers(dest="cmd", metavar="命令")

    sub.add_parser("status", parents=[common], help="服务和索引的状态")

    s = sub.add_parser("search", parents=[common], help="全文搜索")
    s.add_argument("query", help="关键词，空格分开就是 AND")
    s.add_argument("-n", type=int, default=20, help="最多几条（默认 20）")
    s.add_argument("--offset", type=int, default=0, help="从第几条起")
    s.add_argument("--dir", help="只搜这个目录")
    s.add_argument("--type", help="只搜这些类型：md,html,pdf,xlsx,csv")
    s.add_argument("--since", help="最近 N 天（7）或某天起（2026-09-01）")
    s.add_argument("--sort", choices=("score", "mtime"), help="排序方式")

    s = sub.add_parser("map", parents=[common], help="打印库地图")
    s.add_argument("--dir", help="只画这个目录")
    s.add_argument("--max", type=int, help="每个目录最多列几篇（默认 20）")
    s.add_argument("--depth", type=int, help="目录深度")

    s = sub.add_parser("read", parents=[common], help="读一份笔记的源码")
    s.add_argument("path", help="库相对路径")
    s.add_argument("--section", help="只读这个标题下的一节")
    s.add_argument("--lines", help="只读这几行，写成 A-B")

    s = sub.add_parser("outline", parents=[common], help="标题大纲")
    s.add_argument("path", help="库相对路径")

    s = sub.add_parser("recent", parents=[common], help="最近改动过的笔记")
    s.add_argument("--days", type=int, default=7, help="往回看几天（默认 7）")
    s.add_argument("-n", type=int, default=50, help="最多几条（默认 50）")
    s.add_argument("--type", help="只看这些类型")

    s = sub.add_parser("links", parents=[common], help="出链和反链")
    s.add_argument("path", help="库相对路径")

    sub.add_parser("tree", parents=[common], help="库里所有 md / html")

    s = sub.add_parser("new", parents=[common], help="新建一份笔记")
    s.add_argument("title", help="标题，会当成文件名")
    s.add_argument("--dir", help="放进哪个目录（默认随手记目录）")
    s.add_argument("--body-file", metavar="文件", help="正文文件；不给就读 stdin")

    s = sub.add_parser("save", parents=[common], help="整篇覆写一份笔记")
    s.add_argument("path", help="库相对路径")
    s.add_argument("--body-file", metavar="文件", help="正文文件；不给就读 stdin")
    s.add_argument("--based", metavar="改于",
                   help="你读到的那一版的「改于」；不给就用上次 read 记下的那个")
    s.add_argument("--force", action="store_true", help="冲突时也照写")

    s = sub.add_parser("trash", parents=[common], help="把一份挪进废纸篓")
    s.add_argument("path", help="库相对路径")

    sub.add_parser("mcp", parents=[common], help="以 MCP 服务的身份跑（stdio）")

    sub.add_parser("install-skill", parents=[common],
                   help="把 Claude Code 的 amnote skill 装到家目录")
    s = sub.add_parser("agents-md", parents=[common],
                       help="库根的 AGENTS.md")
    s.add_argument("--write", action="store_true", help="真的写出来")
    s = sub.add_parser("mcp-config", parents=[common],
                       help="打印接入用的 MCP 配置")
    s.add_argument("client", nargs="?", choices=("claude", "codex"),
                   default="claude", help="给谁看（默认 claude）")
    return p


HANDLERS = {
    "status": cmd_status, "search": cmd_search, "map": cmd_map,
    "read": cmd_read, "outline": cmd_outline, "tree": cmd_tree,
    "recent": cmd_recent, "links": cmd_links, "new": cmd_new,
    "save": cmd_save, "trash": cmd_trash,
    "install-skill": cmd_install_skill, "agents-md": cmd_agents_md,
    "mcp-config": cmd_mcp_config,
}


def make_source(vault, agent, cmd, quiet=False):
    """先找服务；找不到就看能不能离线。cmd 决定离线够不够用。"""
    src = discover(vault, agent)
    if src:
        return src
    db = os.path.join(vault, ".amnote", "fulltext.db") if vault else ""
    if cmd in READ_CMDS and db and os.path.exists(db):
        if not quiet:
            _note("AM·Note 没在运行，用的是上次的索引。")
        return Offline(vault)
    if not vault:
        raise Fail("不知道要用哪个笔记库。加 --vault <文件夹>，"
                   "或者设 AMNOTE_VAULT。", 2)
    if cmd in READ_CMDS:
        raise Fail("AM·Note 没在运行，%s 里也没有上次的索引。打开 AM·Note 再试。"
                   % os.path.join(vault, ".amnote"), 2)
    if cmd in WRITE_CMDS:
        raise Fail("AM·Note 没在运行。写笔记要先打开它。", 2)
    raise Fail("AM·Note 没在运行。打开它再试。", 2)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    args = p.parse_args(argv)
    cmd = getattr(args, "cmd", None)
    if not cmd:
        p.print_help()
        return 1
    # SUPPRESS 的默认值＝没给就没有这个属性，子命令上给的会盖掉总的那份
    args.json = bool(getattr(args, "json", False))
    vault = resolve_vault(getattr(args, "vault", None))
    agent = agent_name(getattr(args, "agent", None))

    if cmd == "mcp":
        holder = {"src": None}

        def getsrc():
            if holder["src"] is None or holder["src"].kind == "offline":
                try:
                    holder["src"] = make_source(vault, agent, "search",
                                                quiet=True)
                except Fail:
                    holder["src"] = None
                    raise
            return holder["src"]
        getsrc.invalidate = lambda: holder.__setitem__("src", None)
        run_mcp(getsrc)
        return 0

    src = make_source(vault, agent, cmd)
    try:
        HANDLERS[cmd](src, args)
    except Unreachable:
        raise Fail("跟 AM·Note 的连接断了。它还开着吗？", 2)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as e:
        _note(str(e.msg))
        sys.exit(e.code)
    except Unreachable:
        _note("连不上 AM·Note 的门户服务。")
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
