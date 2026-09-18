#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AM·Note 多笔记本验收（5.7）。纯标准库，自己建库、自己起服务、自己收摊。

    python3 scripts/notebooks_check.py            # 全部跑一遍，有一条不过退 1
    python3 scripts/notebooks_check.py -v         # 每条都打出来
    python3 scripts/notebooks_check.py --keep     # 跑完不删临时库（排查用）

临时库建在 `$AMN_CHECK_DIR`（不设就用系统临时目录）底下，端口在 8948–8950。
**一个字节都不碰用户的库和 ~/Library/Application Support/AMNote**：
support-dir、token 文件、notebooks.json、AMNOTE_HOME 全指到临时目录。

十组：
    1 单库零变化   同一个库先用 HEAD 那版服务跑一遍，再用工作区这版跑一遍，
                   十二条路由的响应逐键比对（只许多出 NEW_KEYS 里那几个）
    2 多库路由     前缀、扇出归并、隐含 nb、同名文件互不串、/__locate、nb= 写错
    3 写与流水     新建 / 冲突 / 废纸篓撤销按本隔离 / 门户认领不串库
    4 /__notebooks 添加、撞名默认名、嵌套、改名、颜色、默认、移除、模式翻转
    5 离线         文件夹挪走 → 离线 → 挪回来 → 就绪
    6 静态         图片发得出、.amnote／软链接出库／软链接绕回 .amnote 一律 404
    7 持久化边界   没给 --notebooks-file 不落盘；给了重启还在；跟 --root 取并集
    8 SIGTERM      口令文件收走
    9 笔记本对象   移除／重新定位撞上正在跑的扫描（直接 import fulltext，线程 ＋
                   事件模拟）；库根不在时扫一趟不清库
    10 启动列表    手改过的 notebooks.json 里嵌套／重复的那几条，加载时丢掉；
                   首启 --root 指到 support dir/Welcome 能起，给了列表则不并进去
"""

import argparse
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
PORT_FROM, PORT_TO = 8948, 8950

# 每次跑都会变的键，比对时跳过（值变了不说明坏了）
VOLATILE = {"生成时间", "上次扫描", "上次同步", "端口", "秒前", "耗时", "耗时秒",
            "地图"}
# 新版只许多出这几个键（接口版本 2 → 3 也放行）
# 5.9 添 骨架：/__tree 的每条 文档[] 多一列「骨架」（md 的结构缩影，
# fulltext.skeleton_of 索引时算好存在库里，首页卡片照它画一张纸）。是新增字段，
# 旧键一个没动、值也没变，所以按 NEW_KEYS 放行，不是「单库有变化」。
NEW_KEYS = {"模式", "笔记本", "接口版本", "骨架"}

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

_n_ok = 0
_n_bad = 0
_verbose = False


def ok(cond, what, extra=""):
    """一条断言。返回 bool，方便调用方接着往下判。"""
    global _n_ok, _n_bad
    if cond:
        _n_ok += 1
        if _verbose:
            print("  ✓ %s" % what)
    else:
        _n_bad += 1
        print("  ✗ %s%s" % (what, ("  ← %s" % extra) if extra else ""))
    return bool(cond)


def head(title):
    print("\n── %s " % title + "─" * max(0, 56 - len(title)))


# ── 临时库 ──────────────────────────────────────────────────

def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build_libs(base):
    """三个库，故意撞名：三本都有「会议」，其中两本都有「会议/周会.md」。
    一本有随手记、一本有 html 和图片、一本的路径带空格。"""
    libs = {}
    libs["工作"] = os.path.join(base, "libs", "工作")
    libs["读书"] = os.path.join(base, "libs", "读书笔记")
    libs["项目 Alpha"] = os.path.join(base, "libs", "项目 Alpha")   # 路径带空格

    w(os.path.join(libs["工作"], "会议", "周会.md"),
      "# 周会\n\n本周要点，见 [[发布检查表]]。\n")
    w(os.path.join(libs["工作"], "发布检查表.md"), "# 发布检查表\n\n工作本的。\n")
    w(os.path.join(libs["工作"], "随手记", "灵感.md"), "# 灵感\n\n随手写的。\n")
    w(os.path.join(libs["工作"], "设计", "图表.html"),
      "<html><head><title>图表</title></head><body>柱状图</body></html>")
    os.makedirs(os.path.join(libs["工作"], "设计"), exist_ok=True)
    with open(os.path.join(libs["工作"], "设计", "图.png"), "wb") as f:
        f.write(PNG)

    w(os.path.join(libs["读书"], "会议", "读书会.md"),
      "# 读书会\n\n这里也提 [[发布检查表]]，但这本没有那一份。\n")
    w(os.path.join(libs["读书"], "笔记.md"), "# 读书笔记\n\n卡尔维诺。\n")

    w(os.path.join(libs["项目 Alpha"], "会议", "周会.md"),
      "# 周会\n\n项目 Alpha 的周会记录。\n")
    w(os.path.join(libs["项目 Alpha"], "发布检查表.md"),
      "# 发布检查表\n\n项目 Alpha 本的。\n")

    for p in libs.values():                       # 端口段写死，别去撞用户的实例
        w(os.path.join(p, ".amnote", "config.json"),
          json.dumps({"端口范围": [PORT_FROM, PORT_TO]}, ensure_ascii=False))
    return libs


def export_old(base):
    """把 HEAD 那三份源码导出来，单库零变化那一组拿它当对照。"""
    old = os.path.join(base, "old")
    os.makedirs(old, exist_ok=True)
    for name in ("portal_server.py", "fulltext.py", "portal_i18n.py"):
        try:
            blob = subprocess.check_output(
                ["git", "show", "HEAD:src/" + name], cwd=ROOT)
        except (subprocess.CalledProcessError, OSError) as e:
            print("取不到 HEAD 的 src/%s：%s" % (name, e))
            return ""
        with open(os.path.join(old, name), "wb") as f:
            f.write(blob)
    return old


# ── 服务进程 ────────────────────────────────────────────────

class Srv(object):
    """起一个 portal_server，说话，收摊。

    起过的都记在 `live` 里：中间哪一步抛了异常，main 的 finally 也能一个不落地
    收干净——留一个孤儿进程占着 8948 段，下一次跑就换了端口，很难看出来。
    """

    live = []

    @classmethod
    def kill_all(cls):
        for s in list(cls.live):
            s.stop()

    def __init__(self, base, tag, roots=(), nbfile=None, src_dir=None):
        self.base = base
        self.tag = tag
        self.roots = list(roots)
        self.nbfile = nbfile
        self.src_dir = src_dir or SRC
        self.token_file = os.path.join(base, "sup", "%s.token" % tag)
        self.proc = None
        self.port = 0
        self.token = ""

    def start(self, wait=25.0):
        argv = [sys.executable, os.path.join(self.src_dir, "portal_server.py"),
                "--support-dir", os.path.join(self.base, "sup"),
                "--token-file", self.token_file]
        for r in self.roots:
            argv += ["--root", r]
        if self.nbfile:
            argv += ["--notebooks-file", self.nbfile]
        env = dict(os.environ)
        env["AMNOTE_HOME"] = os.path.join(self.base, "home")
        env.pop("AMNOTE_VAULT", None)
        env.pop("AMNOTE_SUPPORT_DIR", None)
        self.err = open(os.path.join(self.base, "%s.err" % self.tag), "w+b")
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                     stderr=self.err, env=env, cwd=self.base)
        Srv.live.append(self)
        line = self.proc.stdout.readline().decode("utf-8", "replace").strip()
        if not line.isdigit():
            print("起不来（%s）：%s\n%s" % (self.tag, line, self.stderr_text()))
            return False
        self.port = int(line)
        for _ in range(int(wait * 20)):
            try:
                with open(self.token_file, encoding="utf-8") as f:
                    self.token = f.read().strip()
                if self.token:
                    break
            except OSError:
                pass
            time.sleep(0.05)
        return bool(self.token)

    def stderr_text(self):
        try:
            self.err.flush()
            self.err.seek(0)
            return self.err.read().decode("utf-8", "replace")
        except Exception:
            return ""

    # -- HTTP --------------------------------------------------

    def _url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def raw(self, path, headers=None):
        """(状态码, 字节)。404 之类不抛。"""
        req = urllib.request.Request(self._url(path), headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except urllib.error.URLError as e:
            return 0, str(e).encode()

    def get(self, path):
        code, body = self.raw(path)
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            return {"ok": False, "错误": "不是 JSON（%s）：%s" % (code, body[:200])}

    def post(self, path, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url(path), data=data,
            headers={"X-AMN-Token": self.token,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except ValueError:
                return {"ok": False, "错误": "HTTP %s" % e.code}
        except (urllib.error.URLError, ValueError) as e:
            return {"ok": False, "错误": str(e)}

    def wait_ready(self, want=None, secs=30.0):
        """等到没有一本在扫、而且各本都收够了文件。"""
        end = time.time() + secs
        last = {}
        while time.time() < end:
            st = self.get("/__status")
            books = st.get("笔记本") or []
            last = {b["名字"]: b for b in books}
            busy = any(b["状态"] == "扫描中" for b in books)
            enough = all(last.get(k, {}).get("收录", 0) >= v
                         for k, v in (want or {}).items())
            if not busy and st.get("状态") == "就绪" and enough:
                return True
            time.sleep(0.2)
        print("    等不到就绪：%s" % json.dumps(last, ensure_ascii=False)[:300])
        return False

    def stop(self, sig=signal.SIGTERM):
        if not self.proc:
            return
        try:
            self.proc.send_signal(sig)
            self.proc.wait(timeout=15)
        except Exception:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:
                pass
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        self.proc = None
        if self in Srv.live:
            Srv.live.remove(self)


def q(s):
    return urllib.parse.quote(str(s), safe="")


# ── 1. 单库零变化 ───────────────────────────────────────────

SNAP = [
    ("status", "/__status"),
    ("tree", "/__tree"),
    ("search", "/__search?q=" + q("周会")),
    ("raw", "/__raw?path=" + q("会议/周会.md")),
    ("meta", "/__meta?path=" + q("会议/周会.md")),
    ("outline", "/__outline?path=" + q("会议/周会.md")),
    ("links", "/__links?path=" + q("会议/周会.md")),
    ("recent", "/__recent"),
    ("map", "/__map"),
    ("config", "/__config"),
    ("pulse", "/__pulse"),
    ("changes", "/__changes"),
]


def diff(a, b, path=""):
    """老响应 a 跟新响应 b 比。返回一串说明；空＝一样。

    新版只许**多**出 NEW_KEYS 里那几个键；旧键的值必须相等（易变的除外，
    浮点差一点点算相等——分数里掺了「有多新」那一项）。
    """
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k in VOLATILE:
                continue
            if k not in b:
                out.append("%s.%s 没了" % (path, k))
                continue
            if k == "接口版本":
                if not (a[k] == 2 and b[k] == 3) and a[k] != b[k]:
                    out.append("%s.接口版本 %r → %r" % (path, a[k], b[k]))
                continue
            out += diff(a[k], b[k], "%s.%s" % (path, k))
        for k in b:
            if k not in a and k not in NEW_KEYS:
                out.append("%s.%s 是新冒出来的" % (path, k))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return ["%s 条数 %d → %d" % (path, len(a), len(b))]
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, "%s[%d]" % (path, i))
        return out
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a is b else ["%s %r → %r" % (path, a, b)]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, int) and isinstance(b, int):
            return [] if a == b else ["%s %r → %r" % (path, a, b)]
        return [] if abs(a - b) <= 0.02 else ["%s %r → %r" % (path, a, b)]
    return [] if a == b else ["%s %r → %r" % (path, a, b)]


def step_single(base, libs):
    head("1 单库零变化（HEAD 那版 ↔ 这一版）")
    old_dir = export_old(base)
    if not old_dir:
        return ok(False, "导出 HEAD 的源码")
    lib = libs["工作"]
    before, after = {}, {}
    o = Srv(base, "old", roots=[lib], src_dir=old_dir)
    if not ok(o.start(), "HEAD 那版起得来"):
        return False
    o.wait_ready(secs=30)
    for name, path in SNAP:
        before[name] = o.get(path)
    o.stop()
    n = Srv(base, "new1", roots=[lib])
    if not ok(n.start(), "这一版起得来"):
        return False
    n.wait_ready({"工作": 4}, secs=30)
    for name, path in SNAP:
        after[name] = n.get(path)
    good = True
    for name, _ in SNAP:
        d = diff(before[name], after[name], name)
        good &= ok(not d, "/__%s 逐键一样" % name, "；".join(d[:4]))
    # 地图正文是排版出来的一大段，单独比（只有生成时间那一行会变）
    a = [l for l in (before["map"].get("地图") or "").split("\n")
         if not l.startswith("生成于")]
    b = [l for l in (after["map"].get("地图") or "").split("\n")
         if not l.startswith("生成于")]
    good &= ok(a == b, "/__map 地图正文一字不差",
               str([x for x in a if x not in b][:2]))
    # 单库时静态服务照旧发库内文件，但 .amnote 那个老口子堵上了
    good &= ok(n.raw("/" + q("设计/图.png"))[0] == 200, "单库静态图片 200")
    good &= ok(n.raw("/.amnote/fulltext.db")[0] == 404, "单库 /.amnote/ 404（原来是能读的）")
    # /__locate：单库时不加前缀，`本` 照样报主笔记本（访达双击那条路）
    lc = n.get("/__locate?abs=" + q(os.path.join(lib, "会议", "周会.md")))
    good &= ok(lc.get("ok") and lc.get("路径") == "会议/周会.md"
               and lc.get("本") == "工作",
               "单库 /__locate 不加前缀", json.dumps(lc, ensure_ascii=False)[:160])
    # 没给 --notebooks-file：加一本只在内存里，不落盘
    r = n.post("/__notebooks", {"动作": "添加", "路径": [libs["读书"]]})
    good &= ok(r.get("ok") and r.get("持久") is False, "临时列表：添加返回 持久:false",
               json.dumps(r, ensure_ascii=False)[:160])
    good &= ok(r.get("模式") == "多", "临时列表：模式翻到「多」")
    good &= ok(not os.path.exists(os.path.join(base, "sup", "notebooks.json")),
               "临时列表：support dir 里没落下 notebooks.json")
    ids = {b["名字"]: b["id"] for b in (r.get("笔记本") or [])}
    r = n.post("/__notebooks", {"动作": "移除", "id": ids.get("读书笔记", "")})
    good &= ok(r.get("ok") and r.get("模式") == "单", "临时列表：移回一本，模式回「单」")
    t = n.get("/__tree")
    good &= ok(all("/" not in d["路径"] or not d["路径"].startswith("工作/")
                   for d in t.get("文档") or []), "移回一本后路径不带前缀")
    n.stop()
    ok(not os.path.exists(n.token_file), "SIGTERM 之后口令文件被收走")
    return good


# ── 2–3. 多库路由、写入与流水 ───────────────────────────────

def step_multi(base, libs):
    head("2 多库路由")
    nb = os.path.join(base, "sup", "notebooks.json")
    s = Srv(base, "multi", roots=[libs["工作"], libs["读书"], libs["项目 Alpha"]],
            nbfile=nb)
    if not ok(s.start(), "三本一起起得来"):
        return None, False
    good = s.wait_ready({"工作": 4, "读书笔记": 2, "项目 Alpha": 2}, secs=40)
    st = s.get("/__status")
    good &= ok(st.get("模式") == "多", "/__status.模式 == 多")
    books = st.get("笔记本") or []
    good &= ok(len(books) == 3, "/__status.笔记本 三条", str(len(books)))
    good &= ok([b["名字"] for b in books] == ["工作", "读书笔记", "项目 Alpha"],
               "名字默认取文件夹名、顺序＝加入顺序",
               str([b["名字"] for b in books]))
    good &= ok(len({b["颜色"] for b in books}) == 3, "三本三色",
               str([b["颜色"] for b in books]))
    good &= ok(st.get("库根") == libs["工作"], "库根仍报主笔记本")
    good &= ok(books[0]["默认"] and not books[1]["默认"], "默认指针在主笔记本上")

    t = s.get("/__tree")
    docs = t.get("文档") or []
    names = {"工作", "读书笔记", "项目 Alpha"}
    good &= ok(all(d["路径"].split("/")[0] in names for d in docs),
               "/__tree 文档路径全带前缀",
               str([d["路径"] for d in docs][:3]))
    good &= ok(all(d.get("本") in names for d in docs), "/__tree 文档每条带「本」")
    good &= ok(all(d.get("本") in names for d in (t.get("目录") or [])),
               "/__tree 目录每条带「本」")
    good &= ok(sum(1 for d in t.get("目录") or [] if d["名称"] == "会议") == 3,
               "三本各自的「会议」各列一条")
    good &= ok(all(x["路径"].startswith("工作/随手记/") for x in t.get("随手记") or []),
               "随手记带前缀", str(t.get("随手记")))
    good &= ok(t.get("总数") == len(docs) and t.get("总数") >= 8,
               "总数是三本之和", str(t.get("总数")))

    r = s.get("/__search?q=" + q("周会"))
    hits = r.get("结果") or []
    good &= ok(len(hits) == 2, "搜「周会」命中两本各一条", str([h["路径"] for h in hits]))
    good &= ok({h["路径"] for h in hits} ==
               {"工作/会议/周会.md", "项目 Alpha/会议/周会.md"},
               "命中路径带各自的前缀", str([h["路径"] for h in hits]))
    good &= ok(all(h.get("本") for h in hits), "搜索结果带「本」")
    good &= ok(hits == sorted(hits, key=lambda h: (h["档"], -h["分数"])),
               "归并后按档次 ＋ 分数排")
    good &= ok(r.get("总命中") == 2, "总命中是各本之和", str(r.get("总命中")))
    r = s.get("/__search?q=" + q("周会") + "&nb=" + q("工作"))
    good &= ok([h["路径"] for h in r.get("结果") or []] == ["工作/会议/周会.md"],
               "nb= 只搜那一本")
    r = s.get("/__search?q=" + q("周会") + "&dir=" + q("项目 Alpha/会议"))
    good &= ok([h["路径"] for h in r.get("结果") or []] == ["项目 Alpha/会议/周会.md"],
               "带前缀的 dir 隐含了 nb")

    a = s.get("/__raw?path=" + q("工作/会议/周会.md"))
    b = s.get("/__raw?path=" + q("项目 Alpha/会议/周会.md"))
    good &= ok("本周要点" in (a.get("正文") or ""), "同名文件各读各的（工作）")
    good &= ok("项目 Alpha 的周会" in (b.get("正文") or ""), "同名文件各读各的（项目 Alpha）")
    good &= ok(a.get("路径") == "工作/会议/周会.md", "/__raw 回的路径带前缀")
    c = s.get("/__raw?path=" + q("会议/周会.md"))
    good &= ok(c.get("ok") is False and c.get("代码") == "no_notebook",
               "不带前缀的路径 → no_notebook", json.dumps(c, ensure_ascii=False)[:120])

    lk = s.get("/__links?path=" + q("工作/会议/周会.md"))
    outs = {o["目标"]: o for o in lk.get("出链") or []}
    good &= ok(outs.get("发布检查表", {}).get("存在") is True,
               "[[发布检查表]] 在本本里解得开")
    lk2 = s.get("/__links?path=" + q("读书笔记/会议/读书会.md"))
    outs2 = {o["目标"]: o for o in lk2.get("出链") or []}
    good &= ok(outs2.get("发布检查表", {}).get("存在") is False,
               "[[发布检查表]] 不跨本认亲", json.dumps(lk2, ensure_ascii=False)[:160])

    m = s.get("/__map")
    good &= ok(m.get("地图", "").count("# 笔记库地图 · ") == 3, "/__map 全体三节")
    good &= ok("工作/会议/周会.md" in m.get("地图", ""), "地图正文里的路径带前缀")
    m1 = s.get("/__map?nb=" + q("读书笔记"))
    good &= ok(m1.get("地图", "").count("# 笔记库地图 · ") == 1, "/__map?nb= 只画一本")

    rc = s.get("/__recent?n=20")
    good &= ok(len(rc.get("文档") or []) >= 8, "/__recent 合并了三本",
               str(len(rc.get("文档") or [])))
    good &= ok(all(d.get("本") for d in rc.get("文档") or []), "/__recent 每条带「本」")
    times = [d["改于"] for d in rc.get("文档") or []]
    good &= ok(times == sorted(times, reverse=True), "/__recent 按改于降序")
    # 单本快路（nb= 恰好选中一本）跟扇出那条必须给同一形状的路径
    rc1 = s.get("/__recent?n=20&nb=" + q("工作"))
    docs1 = rc1.get("文档") or []
    good &= ok(docs1 and all(d["路径"].startswith("工作/") for d in docs1),
               "/__recent?nb=工作 每条路径以「工作/」开头",
               str([d["路径"] for d in docs1][:3]))
    good &= ok(all(d.get("本") == "工作" for d in docs1),
               "/__recent?nb=工作 每条带「本」")

    cf = s.get("/__config?nb=" + q("读书笔记"))
    good &= ok(cf.get("笔记本") == "读书笔记", "/__config?nb= 读那一本")
    wr = s.post("/__config", {"nb": "读书笔记", "通用标题": ["索引", "读书专用"]})
    good &= ok(wr.get("ok"), "/__config POST 写那一本",
               json.dumps(wr, ensure_ascii=False)[:200])
    good &= ok("读书专用" in (s.get("/__config?nb=" + q("读书笔记"))
                              .get("配置", {}).get("通用标题") or []),
               "写进了读书笔记那一本")
    good &= ok("读书专用" not in (s.get("/__config?nb=" + q("工作"))
                                  .get("配置", {}).get("通用标题") or []),
               "没串到工作那一本")

    mt = s.get("/__meta?path=" + q("项目 Alpha/会议/周会.md"))
    good &= ok(mt.get("路径") == "项目 Alpha/会议/周会.md" and mt.get("标题") == "周会",
               "/__meta 认前缀、回前缀")
    ol = s.get("/__outline?path=" + q("项目 Alpha/会议/周会.md"))
    good &= ok(ol.get("路径") == "项目 Alpha/会议/周会.md"
               and (ol.get("大纲") or [{}])[0].get("文本") == "周会",
               "/__outline 认前缀、回前缀")
    good &= ok(s.get("/__meta?path=" + q("项目 Alpha/没有.md")).get("代码") == "gone",
               "前缀对、文件不在 → gone（不是 no_notebook）")

    ag = s.get("/__agent_setup")
    good &= ok(isinstance(ag.get("agents_md"), list)
               and len(ag["agents_md"]) == 3
               and all(x.get("本") for x in ag["agents_md"]),
               "/__agent_setup 多本时 agents_md 变成按本的数组",
               json.dumps(ag.get("agents_md"), ensure_ascii=False)[:160])
    good &= ok(isinstance(ag.get("地图文件"), list) and len(ag["地图文件"]) == 3,
               "/__agent_setup 地图文件同上")
    good &= step_skill_ver(s, base)
    good &= step_locate(s, base, libs)
    good &= step_nb_arg(s, libs)

    rs = s.post("/__rescan", {})
    good &= ok(rs.get("ok") and len({d["本"] for d in rs.get("文档") or []}) == 3,
               "/__rescan 不带 nb：逐本扫完回合并的树")

    rv = s.post("/__reveal", {"路径": "工作/没有这一份.md"})
    good &= ok(rv.get("代码") == "gone", "/__reveal 前缀路径解析得开（不真开访达）",
               json.dumps(rv, ensure_ascii=False)[:120])
    return s, good


def step_skill_ver(s, base):
    """`/__agent_setup.skill` 的 版本 ／ 最新（页面据此把按钮换成「更新 Skill」）。

    家目录指到临时目录（AMNOTE_HOME），装出来的东西一个字节都不落进用户的
    ~/.claude/。
    """
    latest = 0
    with open(os.path.join(SRC, "agent", "SKILL.md"), encoding="utf-8") as f:
        for line in [f.readline() for _ in range(8)]:
            if "amnote-skill v" in line:
                latest = int(line.split("amnote-skill v")[1].split()[0]
                             .rstrip("-->").strip())
    sk = s.get("/__agent_setup").get("skill") or {}
    good = ok(latest >= 2, "src/agent/SKILL.md 的标记版本 ≥ 2", str(latest))
    good &= ok(sk.get("最新") == latest, "skill.最新 ＝ 随包发的那份的版本",
               json.dumps(sk, ensure_ascii=False)[:200])
    good &= ok(sk.get("已安装") is False and sk.get("版本") == 0,
               "没装过 → 已安装 false、版本 0", json.dumps(sk, ensure_ascii=False)[:200])
    # 手放一份旧的进去：版本读得出、比最新小 → 页面就该提示更新
    dest = os.path.join(base, "home", ".claude", "skills", "amnote", "SKILL.md")
    w(dest, "---\nname: amnote\n---\n<!-- amnote-skill v1 -->\n\n旧的一份。\n")
    sk = s.get("/__agent_setup").get("skill") or {}
    good &= ok(sk.get("已安装") is True and sk.get("版本") == 1,
               "装了一份 v1 → 版本 1", json.dumps(sk, ensure_ascii=False)[:200])
    good &= ok(sk["版本"] < sk["最新"], "版本 < 最新（页面该提示「更新 Skill」）")
    # 别人写的一份（没有标记）读成 0，不该被当成「装好了的新版」
    w(dest, "---\nname: amnote\n---\n\n用户自己写的。\n")
    sk = s.get("/__agent_setup").get("skill") or {}
    good &= ok(sk.get("已安装") is True and sk.get("版本") == 0,
               "没有标记的那一份 → 版本 0", json.dumps(sk, ensure_ascii=False)[:200])
    os.remove(dest)
    r = s.post("/__agent_setup", {"动作": "skill"})
    sk = r.get("skill") or {}
    good &= ok(r.get("ok") is not False and sk.get("版本") == sk.get("最新") == latest,
               "装一遍之后 版本 追上 最新", json.dumps(sk, ensure_ascii=False)[:200])
    return good


def step_locate(s, base, libs):
    """`GET /__locate?abs=…`：绝对路径 → 对外路径（访达双击那条路）。"""
    lc = s.get("/__locate?abs=" + q(os.path.join(libs["工作"], "会议", "周会.md")))
    good = ok(lc.get("ok") and lc.get("路径") == "工作/会议/周会.md"
              and lc.get("本") == "工作", "/__locate 库内绝对路径 → 带前缀的路径",
              json.dumps(lc, ensure_ascii=False)[:160])
    lc = s.get("/__locate?abs=" + q(libs["项目 Alpha"]))
    good &= ok(lc.get("ok") and lc.get("路径") == "项目 Alpha",
               "/__locate 库根本身 → 笔记本名",
               json.dumps(lc, ensure_ascii=False)[:160])
    # realpath：软链接（/private/tmp ↔ /tmp 那一类）两边都解开才对得上
    alias = os.path.join(base, "别名工作")
    if not os.path.lexists(alias):
        os.symlink(libs["工作"], alias)
    lc = s.get("/__locate?abs=" + q(os.path.join(alias, "会议", "周会.md")))
    good &= ok(lc.get("路径") == "工作/会议/周会.md",
               "/__locate 经软链接的绝对路径也认得出",
               json.dumps(lc, ensure_ascii=False)[:160])
    outside = os.path.join(base, "外面的秘密.md")
    w(outside, "# 不该被认成库内\n")
    lc = s.get("/__locate?abs=" + q(outside))
    good &= ok(lc.get("ok") is False and lc.get("代码") == "outside",
               "/__locate 库外 → outside", json.dumps(lc, ensure_ascii=False)[:160])
    lc = s.get("/__locate?abs=" + q(os.path.join(libs["工作"], ".amnote",
                                                 "config.json")))
    good &= ok(lc.get("ok") is False and lc.get("代码") == "bad_path",
               "/__locate 落在 .amnote 里 → bad_path",
               json.dumps(lc, ensure_ascii=False)[:160])
    lc = s.get("/__locate?abs=" + q("会议/周会.md"))
    good &= ok(lc.get("ok") is False and lc.get("代码") == "bad_path",
               "/__locate 给的不是绝对路径 → bad_path",
               json.dumps(lc, ensure_ascii=False)[:160])
    return good


def step_nb_arg(s, libs):
    """`nb=` 认不出的名字一律 no_notebook，**不静默落到主笔记本**。"""
    bad = q("没这一本")
    r = s.get("/__config?nb=" + bad)
    good = ok(r.get("代码") == "no_notebook", "/__config GET 的 nb 写错 → no_notebook",
              json.dumps(r, ensure_ascii=False)[:160])
    r = s.post("/__config", {"nb": "没这一本", "通用标题": ["不该写进去"]})
    good &= ok(r.get("代码") == "no_notebook", "/__config POST 同上",
               json.dumps(r, ensure_ascii=False)[:160])
    good &= ok("不该写进去" not in (s.get("/__config").get("配置", {})
                                    .get("通用标题") or []),
               "而且真的没写进主笔记本")
    r = s.post("/__agent_setup", {"动作": "agents_md", "nb": "没这一本"})
    good &= ok(r.get("代码") == "no_notebook", "/__agent_setup 的 nb 写错 → no_notebook",
               json.dumps(r, ensure_ascii=False)[:160])
    good &= ok(not os.path.lexists(os.path.join(libs["工作"], "AGENTS.md")),
               "没在主笔记本里写出 AGENTS.md")
    r = s.get("/__archive?name=x&nb=" + bad)
    good &= ok(r.get("代码") == "no_notebook", "/__archive 的 nb 写错 → no_notebook",
               json.dumps(r, ensure_ascii=False)[:160])
    r = s.get("/__map?nb=" + bad)
    good &= ok(r.get("代码") == "no_notebook",
               "/__map 的 nb 全写错 → no_notebook（不再报「还没有添加任何笔记本」）",
               json.dumps(r, ensure_ascii=False)[:160])
    return good


def step_write(s, base, libs):
    head("3 写入、废纸篓与流水认领")
    good = True
    # 新建：父目录不存在，但那是随手记目录 → 允许现长出来
    r = s.post("/__save", {"新建": True, "路径": "读书笔记/随手记/临时.md",
                           "正文": "# 临时\n\n新建到另一本的随手记。\n"})
    good &= ok(r.get("ok") and r.get("路径") == "读书笔记/随手记/临时.md",
               "新建到别本的随手记目录", json.dumps(r, ensure_ascii=False)[:160])
    good &= ok(os.path.isfile(os.path.join(libs["读书"], "随手记", "临时.md")),
               "文件真的落在读书笔记那本里")
    based = r.get("改于")
    r = s.post("/__save", {"路径": "读书笔记/随手记/临时.md", "正文": "# 临时\n\n改一版。\n",
                           "基于": "2000-01-01 00:00:00"})
    good &= ok(r.get("需确认") is True and r.get("代码") == "conflict",
               "冲突检测照旧拦一次")
    r = s.post("/__save", {"路径": "读书笔记/随手记/临时.md", "正文": "# 临时\n\n改一版。\n",
                           "基于": based})
    good &= ok(r.get("ok"), "「基于」对得上就存得下")

    # 三张活表按本隔离：A 本刚保存完，先让 B 本跑一趟同步，
    # 认领的那一笔不能被 B 吃掉——A 的流水必须记「门户」
    s.post("/__save", {"路径": "工作/会议/周会.md",
                       "正文": "# 周会\n\n本周要点，见 [[发布检查表]]。改了一版。\n"})
    s.post("/__rescan", {"nb": "项目 Alpha"})      # 先扫别本，故意抢
    s.post("/__rescan", {"nb": "读书笔记"})
    s.post("/__rescan", {"nb": "工作"})
    ch = s.get("/__changes?nb=" + q("工作") + "&n=50")
    flow = ch.get("流水") or []
    mine = [e for e in flow if e.get("路径") == "工作/会议/周会.md"]
    good &= ok(mine and mine[-1].get("来源") == "门户",
               "门户保存的那一笔没被别本认领走（来源＝门户）",
               json.dumps(mine[-1:], ensure_ascii=False)[:200])
    # 单本快路（nb= 恰好选中一本）也要过 out_row
    good &= ok(flow and all(e["路径"].startswith("工作/")
                            for e in flow if e.get("路径")),
               "/__changes?nb=工作 每条路径以「工作/」开头",
               str([e.get("路径") for e in flow][:3]))
    good &= ok(all(e.get("本") == "工作" for e in flow),
               "/__changes?nb=工作 每条带「本」")
    ch = s.get("/__changes?n=50")
    good &= ok(all(e.get("本") for e in ch.get("流水") or []),
               "/__changes 合并后每条带「本」")
    good &= ok(any(e.get("路径", "").startswith("工作/") for e in ch.get("流水") or []),
               "/__changes 合并后路径带前缀")

    # 废纸篓：两本各有一份「会议/周会.md」，撤销不能串
    r1 = s.post("/__trash", {"路径": "工作/会议/周会.md"})
    r2 = s.post("/__trash", {"路径": "项目 Alpha/会议/周会.md"})
    good &= ok(r1.get("ok") and r1.get("路径") == "工作/会议/周会.md", "删得掉（工作）")
    good &= ok(r2.get("ok") and r2.get("路径") == "项目 Alpha/会议/周会.md",
               "删得掉（项目 Alpha）")
    r = s.post("/__untrash", {"路径": "工作/会议/周会.md"})
    good &= ok(r.get("ok"), "撤销（工作）")
    good &= ok(os.path.isfile(os.path.join(libs["工作"], "会议", "周会.md")),
               "工作那份回来了")
    good &= ok(not os.path.isfile(os.path.join(libs["项目 Alpha"], "会议", "周会.md")),
               "项目 Alpha 那份还在废纸篓里（撤销表按本隔离）")
    r = s.post("/__untrash", {"路径": "项目 Alpha/会议/周会.md"})
    good &= ok(r.get("ok") and os.path.isfile(
        os.path.join(libs["项目 Alpha"], "会议", "周会.md")), "项目 Alpha 那份也撤得回来")
    return good


# ── 4. /__notebooks ─────────────────────────────────────────

def step_crud(base, libs):
    head("4 /__notebooks：添加 ／ 改名 ／ 颜色 ／ 默认 ／ 移除")
    nb = os.path.join(base, "sup", "nb2.json")
    s = Srv(base, "crud", roots=[libs["工作"], libs["读书"]], nbfile=nb)
    if not ok(s.start(), "两本起得来"):
        return False
    s.wait_ready(secs=30)
    good = True
    # 撞名默认名：两个都叫「笔记」的文件夹
    a = os.path.join(base, "别处", "iCloud", "笔记")
    b = os.path.join(base, "别处", "本地", "笔记")
    for p in (a, b):
        os.makedirs(p, exist_ok=True)
        w(os.path.join(p, "一.md"), "# 一\n\n内容。\n")
    r = s.post("/__notebooks", {"动作": "添加", "路径": [a]})
    good &= ok(r.get("ok") and any(x["名字"] == "笔记" for x in r["笔记本"]),
               "添加：默认名取文件夹名")
    good &= ok(r.get("变化", {}).get("从") == "多" and r["变化"].get("到") == "多",
               "变化里有 从 / 到")
    good &= ok(r.get("变化", {}).get("主名") == "工作", "变化里有 主名")
    good &= ok(r.get("持久") is True, "给了 --notebooks-file → 持久:true")
    r = s.post("/__notebooks", {"动作": "添加", "路径": [b]})
    good &= ok(r.get("ok") and any(x["名字"] == "本地 笔记" for x in r["笔记本"]),
               "撞名默认名取「父目录名 空格 文件夹名」",
               str([x["名字"] for x in r.get("笔记本") or []]))
    r = s.post("/__notebooks", {"动作": "添加", "路径": [libs["工作"]]})
    good &= ok(r.get("代码") == "nested", "重复添加 → nested", str(r)[:120])
    r = s.post("/__notebooks", {"动作": "添加",
                                "路径": [os.path.join(libs["工作"], "会议")]})
    good &= ok(r.get("代码") == "nested", "把子目录当新本 → nested", str(r)[:120])
    r = s.post("/__notebooks", {"动作": "添加",
                                "路径": [os.path.join(base, "libs")]})
    good &= ok(r.get("代码") == "nested", "把包着两本的父目录当新本 → nested",
               str(r)[:120])
    r = s.post("/__notebooks", {"动作": "添加", "路径": [os.path.join(base, "sup")]})
    good &= ok(r.get("代码") == "not_dir", "support dir 不给当笔记本", str(r)[:120])
    welcome = os.path.join(base, "sup", "Welcome")
    os.makedirs(welcome, exist_ok=True)
    r = s.post("/__notebooks", {"动作": "添加", "路径": [welcome]})
    good &= ok(r.get("代码") == "not_dir", "Welcome 占位根不给当笔记本", str(r)[:120])
    r = s.post("/__notebooks", {"动作": "添加", "路径": [os.path.join(base, "没有这个")]})
    good &= ok(r.get("代码") == "not_dir", "路径不存在 → not_dir", str(r)[:120])

    ids = {x["名字"]: x["id"] for x in s.get("/__notebooks")["笔记本"]}
    r = s.post("/__notebooks", {"动作": "改名", "id": ids["笔记"], "名字": "云笔记"})
    good &= ok(r.get("ok") and r["变化"].get("旧名") == "笔记"
               and r["变化"].get("新名") == "云笔记", "改名：变化里带旧名 / 新名")
    good &= ok(s.get("/__raw?path=" + q("云笔记/一.md")).get("ok"),
               "改名之后新前缀立刻能用")
    r = s.post("/__notebooks", {"动作": "改名", "id": ids["笔记"], "名字": "工作"})
    good &= ok(r.get("代码") == "bad_name", "改名撞名 → bad_name")
    r = s.post("/__notebooks", {"动作": "改名", "id": ids["笔记"], "名字": "a/b"})
    good &= ok(r.get("代码") == "bad_name", "名字带斜杠 → bad_name")
    r = s.post("/__notebooks", {"动作": "改名", "id": ids["笔记"], "名字": ".隐藏"})
    good &= ok(r.get("代码") == "bad_name", "名字以点开头 → bad_name")
    r = s.post("/__notebooks", {"动作": "颜色", "id": ids["笔记"], "颜色": "plum"})
    good &= ok(r.get("ok") and [x for x in r["笔记本"]
                                if x["id"] == ids["笔记"]][0]["颜色"] == "plum",
               "换颜色")
    r = s.post("/__notebooks", {"动作": "颜色", "id": ids["笔记"], "颜色": "陶土色"})
    good &= ok(r.get("代码") == "bad_type", "颜色不在八色盘里 → bad_type")
    r = s.post("/__notebooks", {"动作": "默认", "id": ids["读书笔记"]})
    good &= ok(r.get("默认") == ids["读书笔记"], "设为默认")
    r = s.post("/__notebooks", {"动作": "移除", "id": "不存在的id"})
    good &= ok(r.get("代码") == "no_notebook", "移除不存在的 id → no_notebook")

    # 重新定位：把「本地 笔记」那本挪个地方
    moved = os.path.join(base, "别处", "搬走了")
    shutil.move(b, moved)
    r = s.post("/__notebooks", {"动作": "重新定位", "id": ids["本地 笔记"],
                                "路径": moved})
    good &= ok(r.get("ok"), "重新定位", str(r)[:160])
    good &= ok([x for x in r["笔记本"] if x["id"] == ids["本地 笔记"]][0]["路径"] == moved,
               "位置换了，名字和 id 没变")

    # 一路移除到剩一本：模式翻回「单」，路径不再带前缀
    for name in ("读书笔记", "笔记", "本地 笔记"):   # 「笔记」这会儿叫「云笔记」，id 没变
        r = s.post("/__notebooks", {"动作": "移除", "id": ids[name]})
        good &= ok(r.get("ok"), "移除 %s" % name, str(r)[:120])
    good &= ok(r.get("模式") == "单" and r["变化"]["从"] == "多",
               "剩一本 → 模式翻回「单」")
    t = s.get("/__tree")
    good &= ok(all(not d["路径"].startswith("工作/") for d in t.get("文档") or []),
               "翻回单本后路径不带前缀", str([d["路径"] for d in t.get("文档") or []][:3]))
    good &= ok(s.get("/__raw?path=" + q("会议/周会.md")).get("ok"),
               "翻回单本后不带前缀的路径能读")
    r = s.post("/__notebooks", {"动作": "移除", "id": ids["工作"]})
    good &= ok(r.get("代码") == "last", "移除最后一本 → last", str(r)[:120])
    s.stop()
    return good


# ── 5. 离线 ─────────────────────────────────────────────────

def step_offline(s, base, libs):
    head("5 离线：文件夹挪走再挪回来")
    good = True
    away = libs["读书"] + ".挪走了"
    shutil.move(libs["读书"], away)
    s.get("/__pulse")                             # 探一眼就在这条路上
    st = {b["名字"]: b for b in s.get("/__status")["笔记本"]}
    good &= ok(st.get("读书笔记", {}).get("状态") == "离线",
               "挪走之后 /__pulse 一次就标成离线",
               json.dumps(st.get("读书笔记"), ensure_ascii=False))
    r = s.get("/__raw?path=" + q("读书笔记/笔记.md"))
    good &= ok(r.get("代码") == "offline", "指向离线本的请求 → offline", str(r)[:120])
    good &= ok(s.raw("/" + q("读书笔记/笔记.md"))[0] == 404, "离线本的静态文件 404")
    good &= ok(s.get("/__pulse").get("文件数", 0) > 0, "/__pulse 不把离线本算进去")
    # 趁库根不在的时候硬扫一趟：不能把那一本的索引删光、也不能往流水里灌「删除」
    jr = os.path.join(away, ".amnote", "changes.jsonl")
    j0 = len(open(jr, encoding="utf-8").read().splitlines()) if os.path.exists(jr) else 0
    s.post("/__rescan", {"nb": "读书笔记"})
    j1 = len(open(jr, encoding="utf-8").read().splitlines()) if os.path.exists(jr) else 0
    good &= ok(j1 == j0, "离线时扫一趟：流水里一条「删除」都没多", "%d → %d" % (j0, j1))
    shutil.move(away, libs["读书"])
    s.get("/__pulse")
    s.wait_ready({"读书笔记": 2}, secs=30)
    st = {b["名字"]: b for b in s.get("/__status")["笔记本"]}
    good &= ok(st.get("读书笔记", {}).get("状态") == "就绪", "挪回来之后自动挂上")
    good &= ok(st.get("读书笔记", {}).get("收录", 0) >= 2,
               "索引还在（离线那趟没清库）", json.dumps(st.get("读书笔记"),
                                                       ensure_ascii=False))
    good &= ok(s.get("/__raw?path=" + q("读书笔记/笔记.md")).get("ok"), "又读得到了")
    return good


# ── 6. 静态 ─────────────────────────────────────────────────

def step_static(s, base, libs):
    head("6 静态服务")
    good = True
    good &= ok(s.raw("/" + q("工作/设计/图.png"))[0] == 200, "GET /工作/设计/图.png → 200")
    good &= ok(s.raw("/" + q("工作/.amnote/config.json"))[0] == 404,
               "GET /工作/.amnote/config.json → 404")
    good &= ok(s.raw("/" + q("工作/设计/图表.html"))[0] == 200, "库内 html 发得出")
    good &= ok(s.raw("/" + q("不存在的本/x.md"))[0] == 404, "没这本 → 404")
    good &= ok(s.raw("/" + q("工作/会议/../.amnote/config.json"))[0] == 404,
               "路径里的 .. → 404")
    link = os.path.join(libs["工作"], "外面.md")
    outside = os.path.join(base, "外面的秘密.md")
    w(outside, "# 不该被发出去\n")
    try:
        if not os.path.lexists(link):
            os.symlink(outside, link)
        code = s.raw("/" + q("工作/外面.md"))[0]
        good &= ok(code == 404, "指到库外的软链接 → 404", str(code))
    finally:
        try:
            os.remove(link)
        except OSError:
            pass
    # 段判要判两遍：请求里那几段干干净净，落点却绕回了 .amnote
    back = os.path.join(libs["工作"], "会议", "公开")
    try:
        if not os.path.lexists(back):
            os.symlink(os.path.join("..", ".amnote"), back)
        for tail, what in (("config.json", "config.json"),
                           ("fulltext.db", "fulltext.db")):
            code = s.raw("/" + q("工作/会议/公开/" + tail))[0]
            good &= ok(code == 404,
                       "软链接绕回 .amnote 拿 %s → 404" % what, str(code))
    finally:
        try:
            os.remove(back)
        except OSError:
            pass
    return good


# ── 7. 持久化边界 ───────────────────────────────────────────

def step_persist(base, libs):
    head("7 持久化：重启还在、跟 --root 取并集")
    nb = os.path.join(base, "sup", "notebooks.json")
    good = ok(os.path.isfile(nb), "notebooks.json 落盘了")
    if good:
        good &= ok(oct(os.stat(nb).st_mode & 0o777) == "0o600", "权限 0600")
        data = json.loads(open(nb, encoding="utf-8").read())
        good &= ok(data.get("版本") == 1 and len(data.get("笔记本") or []) == 3,
                   "文件里三本", json.dumps(data, ensure_ascii=False)[:200])
    s = Srv(base, "again", roots=[], nbfile=nb)   # 只给列表，不给 --root
    if not ok(s.start(), "只给 --notebooks-file 也起得来"):
        return False
    s.wait_ready(secs=30)
    names = [b["名字"] for b in s.get("/__notebooks")["笔记本"]]
    good &= ok(names == ["工作", "读书笔记", "项目 Alpha"], "重启之后三本还在", str(names))
    s.stop()
    extra = os.path.join(base, "libs", "新来的")
    w(os.path.join(extra, "一.md"), "# 一\n\n新库。\n")
    s = Srv(base, "union", roots=[extra], nbfile=nb)
    if not ok(s.start(), "列表 ＋ 新 --root 起得来"):
        return False
    s.wait_ready(secs=30)
    names = [b["名字"] for b in s.get("/__notebooks")["笔记本"]]
    good &= ok(names == ["工作", "读书笔记", "项目 Alpha", "新来的"],
               "--notebooks-file ＋ 新 --root 取并集", str(names))
    s.stop()
    data = json.loads(open(nb, encoding="utf-8").read())
    good &= ok(len(data["笔记本"]) == 4, "并集写回了文件")
    # 列表坏了：改名留证，当没有列表处理
    bad = os.path.join(base, "sup", "坏的.json")
    w(bad, "{这不是 JSON")
    s = Srv(base, "broken", roots=[libs["工作"]], nbfile=bad)
    good &= ok(s.start(), "列表读坏了照样起得来")
    if s.proc:
        good &= ok(len(s.get("/__notebooks")["笔记本"]) == 1, "坏列表当没有，用 --root 那本")
        s.stop()
    good &= ok(any(f.startswith("坏的.json.bad-") for f in
                   os.listdir(os.path.join(base, "sup"))), "坏的那份改名留证")
    # 老用户升级：列表还不存在，靠 --root 种出单本列表来（壳正常启动就是这条）
    seed = os.path.join(base, "sup", "seed.json")
    s = Srv(base, "seed", roots=[libs["工作"]], nbfile=seed)
    if ok(s.start(), "列表不存在 ＋ --root 种子起得来"):
        st = s.get("/__status")
        good &= ok(st.get("模式") == "单", "老用户升级：种出来是单本，模式「单」")
        good &= ok(len(st.get("笔记本") or []) == 1, "列表里就一本")
        good &= ok(s.get("/__raw?path=" + q("会议/周会.md")).get("ok"),
                   "路径照旧不带前缀")
        s.stop()
        good &= ok(os.path.isfile(seed), "种完写出了 notebooks.json")

    # 给了列表、文件不在、又没有 --root → 说一句人话然后退出
    p = subprocess.run(
        [sys.executable, os.path.join(SRC, "portal_server.py"),
         "--support-dir", os.path.join(base, "sup"),
         "--token-file", os.path.join(base, "sup", "x.token"),
         "--notebooks-file", os.path.join(base, "sup", "没有这份.json")],
        capture_output=True, timeout=60,
        env=dict(os.environ, AMNOTE_HOME=os.path.join(base, "home"),
                 AMNOTE_VAULT=""))
    good &= ok(p.returncode == 1 and "笔记本" in p.stderr.decode("utf-8", "replace"),
               "空列表 ＋ 没有 --root → exit 1 并说一句人话",
               p.stderr.decode("utf-8", "replace")[:120])
    return good


# ── 9. 笔记本对象：跟正在跑的扫描抢 ─────────────────────────
#
# 这一组不起服务，直接 import fulltext 在本进程里跑：要复现的是「扫到一半那一本
# 被移除／被重新定位／外置盘被拔了」，靠 HTTP 掐不准那个时机，线程 ＋ 事件能。


def step_vault(base):
    head("9 笔记本对象：移除 ／ 重新定位 ／ 库根不在")
    sys.path.insert(0, SRC)
    try:
        import fulltext
    except Exception as e:
        return ok(False, "import fulltext", str(e))
    good = True
    a = os.path.join(base, "vault", "A")
    b = os.path.join(base, "vault", "B")
    for p, name in ((a, "甲"), (b, "乙")):
        w(os.path.join(p, "一.md"), "# %s\n\n内容。\n" % name)
    va = fulltext.register(a, name="A")
    vb = fulltext.register(b, name="B")

    # (1) 一条线程正在 B 本里干活，主线程把 B 移除：那条线程不能掉回 A 本
    inside, go, seen = threading.Event(), threading.Event(), {}
    def worker():
        with fulltext.use(vb):
            inside.set()
            go.wait(10)
            seen["root"] = fulltext.V().root
            seen["journal"] = fulltext.V().journal
    th = threading.Thread(target=worker)
    th.start()
    good &= ok(inside.wait(5), "线程进到 B 本里了")
    fulltext.unregister(vb.vid)
    go.set()
    th.join(10)
    good &= ok(seen.get("root") == vb.root,
               "unregister 之后，跑着的那条线程还在 B 本里（不回落主笔记本）",
               "%s ≠ %s" % (seen.get("root"), vb.root))
    good &= ok(seen.get("journal") == vb.journal,
               "流水也还写在 B 本自己的 .amnote/ 里")
    good &= ok(fulltext.get_vault(vb.vid) is None, "B 本确实被摘掉了")

    # (2) 移除 ／ 重新定位 撞上正在跑的那趟扫描：先等它收尾（拿 sync_lock）
    vb = fulltext.register(b, name="B")
    vb.sync_lock.acquire()                        # 假装「一趟扫描正在跑」
    t0 = time.time()
    fulltext.unregister(vb.vid, wait=0.4)
    dt = time.time() - t0
    vb.sync_lock.release()
    good &= ok(dt >= 0.35, "unregister 会等正在跑的那趟扫描", "只等了 %.2f 秒" % dt)
    vc = fulltext.register(b, name="C")
    moved = os.path.join(base, "vault", "B2")
    os.makedirs(moved, exist_ok=True)
    vc.sync_lock.acquire()
    t0 = time.time()
    vc.point_at(moved, name="C", wait=0.4)
    dt = time.time() - t0
    vc.sync_lock.release()
    good &= ok(dt >= 0.35, "重新定位也等（db／流水／留档目录不能跑到一半换掉）",
               "只等了 %.2f 秒" % dt)
    good &= ok(os.path.realpath(vc.root) == os.path.realpath(moved),
               "等不到也照换位置（用户点的动作不能卡住）")
    fulltext.unregister(vc.vid, wait=0.1)

    # (3) 库根不在了（外置盘拔了）：这一趟扫描什么都不许做。
    #     db 和流水故意留在原地还写得进去——真出事的时候正是这个样子：
    #     句柄还开着，walk_files() 却回空，整本索引会被当成「全删了」。
    fulltext.set_current(va.vid)
    fulltext.sync()
    con = fulltext.connect()
    n0 = con.execute("SELECT COUNT(*) FROM 文档").fetchone()[0]
    con.close()
    good &= ok(n0 >= 1, "A 本先建好索引", str(n0))
    try:
        with open(va.journal, encoding="utf-8") as f:
            j0 = len(f.read().splitlines())
    except OSError:
        j0 = 0
    va.root = os.path.join(base, "vault", "拔掉了")   # 只挪库根，db／流水不动
    r = fulltext.sync()
    good &= ok(r.get("ok") is False and "不在" in str(r.get("说明") or ""),
               "库根不在时扫一趟：直接说这一趟跳过",
               json.dumps(r, ensure_ascii=False)[:160])
    con = fulltext.connect()
    n1 = con.execute("SELECT COUNT(*) FROM 文档").fetchone()[0]
    con.close()
    good &= ok(n1 == n0, "索引一条都没被删掉", "%d → %d" % (n0, n1))
    try:
        with open(va.journal, encoding="utf-8") as f:
            j1 = len(f.read().splitlines())
    except OSError:
        j1 = 0
    good &= ok(j1 == j0, "流水里一条「删除」都没多", "%d → %d" % (j0, j1))

    # (4) configure() ＝「从此只剩这一本」：钉在线程上的那个旧对象也得松开
    fulltext.unregister(va.vid, wait=0.1)
    fulltext.configure(b)
    good &= ok(os.path.realpath(fulltext.V().root) == os.path.realpath(b),
               "configure() 之后 V() 是新登记的那一本（不是还钉着的旧对象）",
               fulltext.V().root)
    return good


# ── 10. 启动时也过嵌套 ／ 重复检查 ──────────────────────────

def step_boot(base, libs):
    head("10 启动：手改过的列表里，嵌套 ／ 重复的那几条丢掉")
    nb = os.path.join(base, "sup", "nested.json")
    rows = [("aaaa1111", "工作", libs["工作"]),
            ("bbbb2222", "会议", os.path.join(libs["工作"], "会议")),  # 套在工作里
            ("cccc3333", "工作副本", libs["工作"]),                    # 同一个位置
            ("dddd4444", "自己人", os.path.join(base, "sup")),         # support dir
            ("eeee5555", "读书笔记", libs["读书"])]
    w(nb, json.dumps(
        {"版本": 1,
         "笔记本": [{"id": i, "名字": n, "路径": p, "颜色": "blue",
                     "加入": "2026-09-07 10:00:00"} for i, n, p in rows],
         "默认": "aaaa1111"}, ensure_ascii=False))
    s = Srv(base, "nested", roots=[], nbfile=nb)
    if not ok(s.start(), "手改过的列表照样起得来"):
        return False
    s.wait_ready(secs=30)
    names = [x["名字"] for x in s.get("/__notebooks")["笔记本"]]
    good = ok(names == ["工作", "读书笔记"],
              "嵌套 ／ 重复 ／ support dir 那三条都丢掉了", str(names))
    err = s.stderr_text()
    good &= ok(err.count("挂不上") == 3, "每条丢掉的都在 stderr 上记了一行",
               err[:200].replace("\n", " ⏎ "))
    good &= ok(s.get("/__raw?path=" + q("工作/会议/周会.md")).get("ok"),
               "留下的那两本照常用")
    s.stop()
    data = json.loads(open(nb, encoding="utf-8").read())
    good &= ok(len(data["笔记本"]) == 2, "写回去的列表也只剩两本",
               json.dumps(data, ensure_ascii=False)[:200])

    # 首启：壳把 --root 指到 support dir/Welcome，且不给 --notebooks-file。
    # 这条根不是笔记本，但服务必须起得来，否则欢迎卡永远出不来。
    welcome = os.path.join(base, "sup", "Welcome")
    os.makedirs(os.path.join(welcome, ".amnote"), exist_ok=True)
    w(os.path.join(welcome, ".amnote", "config.json"),
      json.dumps({"端口范围": [PORT_FROM, PORT_TO]}, ensure_ascii=False))
    s = Srv(base, "welcome", roots=[welcome])
    if not ok(s.start(), "首启占位根 Welcome 起得来（不给 --notebooks-file）"):
        return good
    st = s.get("/__status")
    good &= ok(st.get("ok") is True and st.get("模式") == "单",
               "首启占位根能出 /__status，模式「单」",
               json.dumps(st, ensure_ascii=False)[:200])
    code, body = s.raw("/portal")
    good &= ok(code == 200 and b"<html" in body.lower(),
               "首启占位根能出 /portal", str(code))
    s.stop()

    # 给了列表就不能把 Welcome 并进去（并集写回会在用户名单里多种一本假的）
    nb2 = os.path.join(base, "sup", "with-welcome.json")
    w(nb2, json.dumps(
        {"版本": 1,
         "笔记本": [{"id": "aaaa1111", "名字": "工作", "路径": libs["工作"],
                     "颜色": "blue", "加入": "2026-09-07 10:00:00"}],
         "默认": "aaaa1111"}, ensure_ascii=False))
    s = Srv(base, "welcome-skip", roots=[welcome], nbfile=nb2)
    if not ok(s.start(), "列表 ＋ --root Welcome 起得来"):
        return good
    names = [x["名字"] for x in s.get("/__notebooks")["笔记本"]]
    good &= ok(names == ["工作"], "给了列表时 Welcome 不并进名单", str(names))
    err = s.stderr_text()
    good &= ok("挂不上" in err, "stderr 记了 Welcome 被跳过",
               err[:200].replace("\n", " ⏎ "))
    s.stop()
    data = json.loads(open(nb2, encoding="utf-8").read())
    dumped = json.dumps(data, ensure_ascii=False)
    good &= ok(len(data["笔记本"]) == 1 and "Welcome" not in dumped,
               "写回去的列表没有 Welcome", dumped[:200])
    return good


# ── main ────────────────────────────────────────────────────

def main():
    global _verbose
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true", help="跑完不删临时库")
    ap.add_argument("--dir", default=os.environ.get("AMN_CHECK_DIR") or "",
                    help="临时库建在哪儿（默认系统临时目录）")
    args = ap.parse_args()
    _verbose = args.verbose

    parent = os.path.abspath(os.path.expanduser(args.dir)) if args.dir else None
    if parent:
        os.makedirs(parent, exist_ok=True)
    base = tempfile.mkdtemp(prefix="amnote-nb-", dir=parent)
    os.makedirs(os.path.join(base, "sup"), exist_ok=True)
    os.makedirs(os.path.join(base, "home"), exist_ok=True)
    print("临时库：%s\n端口段：%d–%d" % (base, PORT_FROM, PORT_TO))
    libs = build_libs(base)
    srv = None
    try:
        step_single(base, libs)
        srv, _ = step_multi(base, libs)
        if srv:
            step_write(srv, base, libs)
            step_static(srv, base, libs)
            step_offline(srv, base, libs)
            srv.stop()
            srv = None
        step_crud(base, libs)
        step_persist(base, libs)
        step_boot(base, libs)
        step_vault(base)
    finally:
        Srv.kill_all()
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)
        else:
            print("\n临时库留着了：%s" % base)
    print("\n" + "─" * 60)
    print("过 %d 条，不过 %d 条。" % (_n_ok, _n_bad))
    return 1 if _n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
