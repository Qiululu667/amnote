#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AM·Note · 服务端消息的三语词典  (5.5)

门户的界面文字在网页那边翻（`src/locales/*.js`），这里只管**服务端自己生成的
那些话**：错误提示、保存结果、config.json 的校验意见。

约定（5.5 契约 §4）：
    · 键 ＝ 代码里那句简体中文**格式串**本身，占位符（`{e}` `{n}` `{k}` …）
      原样留在键里。简体中文是源语言，不需要词典——查不到就回落到键。
    · 只翻「值」。JSON 字段名（错误/路径/正文/状态/问题/结果/需确认…）、
      config.json 的配置键、目录名（随手记 / _图 / .amnote）、`状态` 的哨兵值
      （扫描中/就绪/同步中/未建库/异常）一律保持中文，页面按它们比对。
    · `问题` 那几句里的 `「配置键」` 必须原样保留简体中文——页面靠这个字符串
      定位是哪一项设置。英文和繁中在后面补一个括号说明（`{g}`），
      说明由 `gloss()` 按当前语言生成，简体下是空串。
    · 每条消息可以带一个稳定的 ASCII 短码（`CODES`），随响应发成 `"代码"`。
      页面按短码判断分支，不再靠正则匹配中文文案。

用法：
    portal_i18n.set_lang("en")        # 每个请求开头设一次（threading.local）
    portal_i18n.T("文件不在了")        # → Msg("That file is gone", code="gone")

一份 `fulltext.py` 也在用 `T()`（config.json 的校验意见）。命令行跑
fulltext 时没有人调 set_lang，语言就是默认的 zh-Hans，stderr 上还是中文。
"""

import threading

LANGS = ("zh-Hans", "zh-HK", "en")
DEFAULT_LANG = "zh-Hans"

_local = threading.local()


def set_lang(code=None):
    """设定本线程这一趟请求的消息语言。不认的代号一律当 zh-Hans。"""
    _local.lang = code if code in LANGS else DEFAULT_LANG
    return _local.lang


def get_lang():
    return getattr(_local, "lang", DEFAULT_LANG)


class Msg(str):
    """一句给用户看的话，另外挂一个稳定短码。

    是 str 的子类，所以 `{"错误": msg}` 和 json.dumps 都照旧能用；想要短码的
    地方读 `msg.code`（没有就是空串）。
    """

    __slots__ = ("code",)

    def __new__(cls, text, code=""):
        s = super().__new__(cls, text)
        s.code = code
        return s


# ── 稳定短码 ──────────────────────────────────────
#
# 页面按这个判断该走哪条分支（「同名文件已经有了」以前是拿正则匹配中文
# 文案认的，三种语言之后那条正则必然认不出来）。短码只增不改。
#
#   exists    这个名字已经被占了
#   gone      要找的东西不在了
#   too_big   超过大小上限
#   conflict  打开编辑之后被别处改过，要用户拍板
#   bad_path  路径不合法 / 越界 / 这个位置不给动
#   bad_type  类型、格式、参数不对
#   io        读写失败（底下是一条 OSError）
#
# 5.7（多笔记本）另加六个，都只出现在跟笔记本有关的路由上：
#   no_notebook  路径的第一段不是任何一个笔记本的名字 / 没有这个 id / nb= 写错了
#   offline      这一本的文件夹现在不在（外置盘、iCloud 没就绪）
#   nested       两个笔记本套着了，或者这个文件夹已经登记过
#   last         最后一本不给移除
#   bad_name     笔记本的名字不合法或者撞名
#   outside      /__locate 那条：这个绝对路径不在任何一本里（页面转库外只读）

CODES = {
    "同名文件已经有了": "exists",

    # 笔记本（5.7）
    "还没有添加任何笔记本": "no_notebook",
    "路径要从笔记本的名字开始，比如「{n}/…」": "no_notebook",
    "没有这个笔记本": "no_notebook",
    "没有叫「{n}」这个名字的笔记本": "no_notebook",
    "这份不在任何一个笔记本里": "outside",
    "笔记本「{n}」现在找不到，它的文件夹可能被挪走了": "offline",
    "这个文件夹已经是笔记本「{n}」了": "nested",
    "这个文件夹已经在「{n}」里面了": "nested",
    "「{n}」就在这个文件夹里面": "nested",
    "至少要留一个笔记本": "last",
    "笔记本得有个名字": "bad_name",
    "名字里不能有斜杠": "bad_name",
    "名字不能用「.」或「_」开头": "bad_name",
    "名字最多 {n} 个字": "bad_name",
    "已经有一个笔记本叫「{n}」了": "bad_name",
    "要一个文件夹的路径": "not_dir",
    "找不到这个文件夹：{p}": "not_dir",
    "这是 AM·Note 自己的文件夹，不能当笔记本": "not_dir",
    "不认识这个颜色": "bad_type",

    "同名文件刚被建走了，换个标题": "exists",
    "原来那个位置又有文件了，先挪开": "exists",
    "那个位置已经有一份文件了，先挪开": "exists",

    "文件不在了": "gone",
    "这份文件不在了": "gone",
    "库根不在了": "gone",
    "这份撤不回来了，去废纸篓里找": "gone",
    "废纸篓里已经没有这份了": "gone",
    "原来那个目录不在了": "gone",
    "这个文件夹还不存在，先在访达里建好": "gone",
    "这份没登记过，重新拖一次": "gone",
    "这张图不在了": "gone",
    "没有这份留档": "gone",
    "本机没有新细明体": "gone",
    "还没设过头像": "gone",
    "找不到这个小节": "gone",
    "行号超出这份的范围": "gone",
    "缺一份模板：{p}": "gone",

    "新建时正文别超过 1 MB": "too_big",
    "这份太大了（超过 8 MB），别在门户里改": "too_big",
    "这张超过 {m} MB 了": "too_big",
    "这张 {n} MB，上限 {m} MB": "too_big",
    "这份 {n} MB，上限 {m} MB": "too_big",
    "这张图太大了": "too_big",
    "头像别超过 {m} MB": "too_big",
    "请求体为空或过大": "too_big",

    "你打开编辑之后，这份在别处被改过（{now}）。继续保存会盖掉那次改动。": "conflict",

    "路径不合法": "bad_path",
    "路径越出库根": "bad_path",
    "这个位置不给写": "bad_path",
    "这个位置不给删": "bad_path",
    "要一个绝对路径": "bad_path",
    "这张图不在文档旁边": "bad_path",
    "只能改文件名": "bad_path",
    "这份在库里，按库内文档打开": "bad_path",

    "这个格式门户不认": "bad_type",
    "只有 md 能在门户里读源码": "bad_type",
    "只有 md 能在门户里改": "bad_type",
    "只能新建 md": "bad_type",
    "只能打开 md": "bad_type",
    "只有笔记和网页能删": "bad_type",
    "只认图片": "bad_type",
    "只收 png / jpg / gif / webp": "bad_type",
    "只收 http / https 图片地址": "bad_type",
    "这个地址不是图片": "bad_type",
    "这个文件不是图片": "bad_type",
    "图片数据不对": "bad_type",
    "图片是空的": "bad_type",
    "正文不能是空的": "bad_type",
    "正文得是文本": "bad_type",
    "配置得是一个对象": "bad_type",
    "「{k}」类型不对": "bad_type",
    "端口范围要填两个 1-65535 的整数，前小后大": "bad_type",
    "请求不是合法 JSON：{e}": "bad_type",
    "行号要写成 A-B，都从 1 数起": "bad_type",
    "since 要写成天数（7）或日期（2026-09-01）": "bad_type",
    "不认识这个动作": "bad_type",

    "读不了：{e}": "io",
    "读不了原文：{e}": "io",
    "读不了这张图：{e}": "io",
    "拉不下来这张图": "io",
    "写不进去：{e}": "io",
    "写失败：{e}": "io",
    "图片写不进去：{e}": "io",
    "改名失败：{e}": "io",
    "打不开访达：{e}": "io",
    "打不开：{e}": "io",
    "打不开废纸篓：{e}": "io",
    "挪不进废纸篓：{e}": "io",
    "挪不回去：{e}": "io",
    "fulltext.db 读不了：{e}（跑一次重扫）": "io",
}


# ── config.json 配置键的括号说明 ───────────────────
#
# `问题` 里的 `「端口范围」` 得原样是简体中文（页面靠它定位是哪一项），
# 但英文读者看不懂，所以后面补一个括号说明。简体下是空串。

CONFIG_GLOSS = {
    "en": {
        "跳过目录关键词": "skip folders containing",
        "噪声目录": "ignored folders",
        "噪声文件": "ignored files",
        "通用标题": "generic titles",
        "板块名": "section names",
        "端口范围": "port range",
        "随手记目录": "Quick Notes folder",
    },
    "zh-HK": {
        "跳过目录关键词": "略過的資料夾關鍵字",
        "噪声目录": "忽略的資料夾",
        "噪声文件": "忽略的檔案",
        "通用标题": "通用標題",
        "板块名": "板塊名稱",
        "端口范围": "連接埠範圍",
        "随手记目录": "隨手記資料夾",
    },
}


def gloss(key):
    """配置键后面那一小段括号说明。简体（和查不到的键）返回空串。"""
    lang = get_lang()
    word = (CONFIG_GLOSS.get(lang) or {}).get(key)
    if not word:
        return ""
    return " (%s)" % word if lang == "en" else "（%s）" % word


MESSAGES = {

    # ── English ────────────────────────────────────
    #
    # 提示与正文 sentence case；短句不加句号，成句的才加（跟源串一个口径）。
    # 「门户」在英文里就说 AM·Note，不另造一个词。
    "en": {
        # 门禁
        "Host 不对": "Wrong Host header",
        "跨站请求不收": "Cross-site requests aren't accepted",
        "口令不对。退出 AM·Note 再打开一次。":
            "Wrong token. Quit AM·Note and open it again.",
        "重扫要用 POST": "Rescan needs a POST request",
        "请求体为空或过大": "The request body is empty or too large",
        "请求不是合法 JSON：{e}": "The request isn't valid JSON: {e}",

        # 路径与格式
        "路径不合法": "That path isn't valid",
        "路径越出库根": "That path is outside the vault",
        "文件不在了": "That file is gone",
        "这个格式门户不认": "AM·Note doesn't handle this file type",
        "库根不在了": "The vault folder is gone",
        "这个位置不给写": "This location isn't writable",
        "这个位置不给删": "Files here can't be deleted",
        "只有 md 能在门户里读源码": "Only Markdown files can be read as source",
        "只有 md 能在门户里改": "Only Markdown files can be edited here",
        "只能新建 md": "New files have to be Markdown",
        "只有笔记和网页能删": "Only notes and web pages can be deleted",

        # 访达 ／ 系统默认程序
        "打不开访达：{e}": "Couldn't open Finder: {e}",
        "打不开：{e}": "Couldn't open it: {e}",

        # 读
        "读不了：{e}": "Couldn't read it: {e}",
        "读不了原文：{e}": "Couldn't read the original: {e}",
        "fulltext.db 读不了：{e}（跑一次重扫）":
            "Couldn't read fulltext.db: {e} (try a rescan)",
        "没有这份留档": "No such archived version",

        # 新建
        "正文不能是空的": "The body can't be empty",
        "新建时正文别超过 1 MB": "A new note's body can't be larger than 1 MB",
        "这个文件夹还不存在，先在访达里建好":
            "That folder doesn't exist yet — create it in Finder first",
        "同名文件已经有了": "A file with that name already exists",
        "同名文件刚被建走了，换个标题":
            "A file with that name was just created — pick another title",
        "写不进去：{e}": "Couldn't write it: {e}",

        # 贴图
        "这张超过 {m} MB 了": "That image is larger than {m} MB",
        "图片数据不对": "The image data isn't valid",
        "图片是空的": "The image is empty",
        "这张 {n} MB，上限 {m} MB": "That image is {n} MB; the limit is {m} MB",
        "只收 png / jpg / gif / webp": "Only PNG, JPEG, GIF and WebP are accepted",
        "只收 http / https 图片地址": "Image addresses have to be http or https",
        "这个地址不是图片": "That address isn't an image",
        "这个文件不是图片": "That file isn't an image",
        "拉不下来这张图": "Couldn't download that image",
        "读不了这张图：{e}": "Couldn't read that image: {e}",
        "图片写不进去：{e}": "Couldn't save the image: {e}",

        # 保存与改名
        "只能改文件名": "Only the file name can be changed",
        "改名失败：{e}": "Couldn't rename it: {e}",
        "正文得是文本": "The body has to be text",
        "这份太大了（超过 8 MB），别在门户里改":
            "This note is larger than 8 MB — too big to edit here",
        "没有改动": "No changes",
        "你打开编辑之后，这份在别处被改过（{now}）。继续保存会盖掉那次改动。":
            "This note was changed elsewhere ({now}) after you started editing. "
            "Saving now will overwrite that change.",
        "写失败：{e}": "Save failed: {e}",
        "已保存": "Saved",

        # 废纸篓
        "打不开废纸篓：{e}": "Couldn't open the Trash: {e}",
        "挪不进废纸篓：{e}": "Couldn't move it to the Trash: {e}",
        "这份撤不回来了，去废纸篓里找":
            "This can't be undone any more — look in the Trash",
        "废纸篓里已经没有这份了": "It isn't in the Trash any more",
        "原来那个位置又有文件了，先挪开":
            "Something else is in its old spot — move that first",
        "原来那个目录不在了": "Its original folder is gone",
        "挪不回去：{e}": "Couldn't move it back: {e}",

        # 设置
        "配置得是一个对象": "Settings have to be an object",
        "「{k}」类型不对": "「{k}」{g} has the wrong type",
        "端口范围要填两个 1-65535 的整数，前小后大":
            "Port range needs two integers from 1 to 65535, smaller one first",

        # config.json 的校验意见（成句，带句号）
        "config.json 读不了，整份用默认值：{e}":
            "Couldn't read config.json; using all the defaults: {e}",
        "config.json 不是一个对象，整份用默认值":
            "config.json isn't an object; using all the defaults.",
        "config.json 的「{k}」类型不对，这一项用默认值":
            "「{k}」{g} in config.json has the wrong type; using the default.",
        "config.json 的「{k}」是空的，这一项用默认值":
            "「{k}」{g} in config.json is empty; using the default.",
        "config.json 的「端口范围」不合法（{pr}），用默认值":
            "「端口范围」 (port range) in config.json isn't valid ({pr}); "
            "using the default.",

        # 库外文档
        "要一个绝对路径": "An absolute path is required",
        "只能打开 md": "Only Markdown files can be opened",
        "这份文件不在了": "That file is gone",
        "这份在库里，按库内文档打开":
            "This one is in the vault — open it as a vault note",
        "这份 {n} MB，上限 {m} MB": "That file is {n} MB; the limit is {m} MB",
        "这份没登记过，重新拖一次":
            "This one isn't registered any more — drag it in again",
        "这张图不在文档旁边": "That image isn't next to the document",
        "只认图片": "Images only",
        "这张图不在了": "That image is gone",
        "这张图太大了": "That image is too large",

        # 字体
        "本机没有新细明体": "PMingLiU isn't available on this Mac",

        # 个人资料（5.6）
        "还没设过头像": "No avatar has been set",
        "头像别超过 {m} MB": "An avatar can't be larger than {m} MB",

        # 取一段正文 ／ 大纲（5.6）
        "找不到这个小节": "No section by that name",
        "行号要写成 A-B，都从 1 数起":
            "Line range has to look like A-B, counting from 1",
        "行号超出这份的范围": "That line range is past the end of this file",
        "since 要写成天数（7）或日期（2026-09-01）":
            "since has to be a number of days (7) or a date (2026-09-01)",

        # 笔记本（5.7）
        "还没有添加任何笔记本": "No notebook has been added yet",
        "路径要从笔记本的名字开始，比如「{n}/…」":
            "Paths have to start with a notebook name, like 「{n}/…」",
        "没有这个笔记本": "No such notebook",
        "没有叫「{n}」这个名字的笔记本": "There's no notebook called 「{n}」",
        "这份不在任何一个笔记本里": "That file isn't inside any notebook",
        "笔记本「{n}」现在找不到，它的文件夹可能被挪走了":
            "Notebook 「{n}」 can't be found — its folder may have been moved",
        "这个文件夹已经是笔记本「{n}」了":
            "That folder is already the notebook 「{n}」",
        "这个文件夹已经在「{n}」里面了": "That folder is already inside 「{n}」",
        "「{n}」就在这个文件夹里面": "「{n}」 is inside that folder",
        "至少要留一个笔记本": "At least one notebook has to stay",
        "笔记本得有个名字": "A notebook needs a name",
        "名字里不能有斜杠": "A name can't contain slashes",
        "名字不能用「.」或「_」开头": "A name can't start with 「.」 or 「_」",
        "名字最多 {n} 个字": "A name can be at most {n} characters",
        "已经有一个笔记本叫「{n}」了": "There's already a notebook called 「{n}」",
        "要一个文件夹的路径": "A folder path is required",
        "找不到这个文件夹：{p}": "That folder doesn't exist: {p}",
        "这是 AM·Note 自己的文件夹，不能当笔记本":
            "That's AM·Note's own folder — it can't be a notebook",
        "不认识这个颜色": "That isn't one of the notebook colors",

        # 接入向导（5.6）
        "不认识这个动作": "That isn't something AM·Note can set up",
        "缺一份模板：{p}": "A template file is missing: {p}",
        "那个位置已经有一份文件了，先挪开":
            "There's already a file there — move it aside first",
        "命令行工具装好了：{p}": "The command line tool is set up: {p}",
        "Skill 装好了：{p}": "The skill is installed: {p}",
        "AGENTS.md 写好了：{p}": "AGENTS.md is written: {p}",
        "库地图导出好了，{n} 篇：{p}":
            "The vault map is exported, {n} notes: {p}",
    },

    # ── 繁體中文（香港）─────────────────────────────
    #
    # 用語以 macOS 香港版為準：檔案 / 資料夾 / 儲存 / 垃圾桶 / Finder（不譯）/
    # 結束（Quit）/ 設定 / 連接埠。
    "zh-HK": {
        # 門禁
        "Host 不对": "Host 不對",
        "跨站请求不收": "不接受跨站請求",
        "口令不对。退出 AM·Note 再打开一次。":
            "口令不對。結束 AM·Note 再打開一次。",
        "重扫要用 POST": "重掃要用 POST 請求",
        "请求体为空或过大": "請求內容是空的，或者太大",
        "请求不是合法 JSON：{e}": "請求不是有效的 JSON：{e}",

        # 路徑與格式
        "路径不合法": "路徑不合法",
        "路径越出库根": "路徑超出筆記庫範圍",
        "文件不在了": "檔案不在了",
        "这个格式门户不认": "AM·Note 不支援這種檔案格式",
        "库根不在了": "筆記庫資料夾不在了",
        "这个位置不给写": "這個位置不能寫入",
        "这个位置不给删": "這個位置的檔案不能刪除",
        "只有 md 能在门户里读源码": "只有 Markdown 檔案能看原始碼",
        "只有 md 能在门户里改": "只有 Markdown 檔案能在這裡編輯",
        "只能新建 md": "只能新增 Markdown 檔案",
        "只有笔记和网页能删": "只有筆記和網頁能刪除",

        # Finder ／ 預設程式
        "打不开访达：{e}": "打不開 Finder：{e}",
        "打不开：{e}": "打不開：{e}",

        # 讀
        "读不了：{e}": "讀不到：{e}",
        "读不了原文：{e}": "讀不到原文：{e}",
        "fulltext.db 读不了：{e}（跑一次重扫）":
            "讀不到 fulltext.db：{e}（重掃一次試試）",
        "没有这份留档": "沒有這一份存檔",

        # 新增
        "正文不能是空的": "內容不能是空白",
        "新建时正文别超过 1 MB": "新增筆記的內容不能超過 1 MB",
        "这个文件夹还不存在，先在访达里建好":
            "這個資料夾還未存在，請先在 Finder 裡建立",
        "同名文件已经有了": "已經有同名的檔案",
        "同名文件刚被建走了，换个标题": "剛剛有人建了同名檔案，換一個標題",
        "写不进去：{e}": "寫不進去：{e}",

        # 貼圖
        "这张超过 {m} MB 了": "這張圖超過 {m} MB",
        "图片数据不对": "圖片資料不正確",
        "图片是空的": "圖片是空的",
        "这张 {n} MB，上限 {m} MB": "這張圖 {n} MB，上限 {m} MB",
        "只收 png / jpg / gif / webp": "只接受 PNG、JPEG、GIF、WebP",
        "只收 http / https 图片地址": "圖片網址只接受 http 或 https",
        "这个地址不是图片": "這個網址不是圖片",
        "这个文件不是图片": "這個檔案不是圖片",
        "拉不下来这张图": "拉不下來這張圖",
        "读不了这张图：{e}": "讀不到這張圖：{e}",
        "图片写不进去：{e}": "圖片寫不進去：{e}",

        # 儲存與更名
        "只能改文件名": "只能更改檔案名稱",
        "改名失败：{e}": "更改名稱失敗：{e}",
        "正文得是文本": "內容必須是文字",
        "这份太大了（超过 8 MB），别在门户里改":
            "這份超過 8 MB，太大了，不適合在這裡編輯",
        "没有改动": "沒有改動",
        "你打开编辑之后，这份在别处被改过（{now}）。继续保存会盖掉那次改动。":
            "你開始編輯之後，這份在別處被改過（{now}）。繼續儲存會覆蓋那次改動。",
        "写失败：{e}": "儲存失敗：{e}",
        "已保存": "已儲存",

        # 垃圾桶
        "打不开废纸篓：{e}": "打不開垃圾桶：{e}",
        "挪不进废纸篓：{e}": "移不進垃圾桶：{e}",
        "这份撤不回来了，去废纸篓里找": "這份已經復原不了，到垃圾桶裡找",
        "废纸篓里已经没有这份了": "垃圾桶裡已經沒有這一份",
        "原来那个位置又有文件了，先挪开": "原本的位置又有檔案了，先移開",
        "原来那个目录不在了": "原本的資料夾不在了",
        "挪不回去：{e}": "移不回去：{e}",

        # 設定
        "配置得是一个对象": "設定必須是一個物件",
        "「{k}」类型不对": "「{k}」{g}類型不對",
        "端口范围要填两个 1-65535 的整数，前小后大":
            "連接埠範圍要填兩個 1 至 65535 的整數，小的在前",

        # config.json 的檢查意見
        "config.json 读不了，整份用默认值：{e}":
            "讀不到 config.json，整份用預設值：{e}",
        "config.json 不是一个对象，整份用默认值":
            "config.json 不是一個物件，整份用預設值。",
        "config.json 的「{k}」类型不对，这一项用默认值":
            "config.json 的「{k}」{g}類型不對，這一項用預設值。",
        "config.json 的「{k}」是空的，这一项用默认值":
            "config.json 的「{k}」{g}是空的，這一項用預設值。",
        "config.json 的「端口范围」不合法（{pr}），用默认值":
            "config.json 的「端口范围」（連接埠範圍）不合法（{pr}），用預設值。",

        # 庫外文件
        "要一个绝对路径": "需要一個絕對路徑",
        "只能打开 md": "只能打開 Markdown 檔案",
        "这份文件不在了": "這份檔案不在了",
        "这份在库里，按库内文档打开": "這份在筆記庫裡，用庫內筆記的方式打開",
        "这份 {n} MB，上限 {m} MB": "這份 {n} MB，上限 {m} MB",
        "这份没登记过，重新拖一次": "這份未登記過，再拖一次",
        "这张图不在文档旁边": "這張圖不在文件旁邊",
        "只认图片": "只接受圖片",
        "这张图不在了": "這張圖不在了",
        "这张图太大了": "這張圖太大了",

        # 字體
        "本机没有新细明体": "這部 Mac 上沒有新細明體",

        # 個人資料（5.6）
        "还没设过头像": "還未設定過大頭貼",
        "头像别超过 {m} MB": "大頭貼不能超過 {m} MB",

        # 取一段正文 ／ 大綱（5.6）
        "找不到这个小节": "找不到這個小節",
        "行号要写成 A-B，都从 1 数起": "行號要寫成 A-B，都由 1 開始數",
        "行号超出这份的范围": "行號超出這份的範圍",
        "since 要写成天数（7）或日期（2026-09-01）":
            "since 要寫成日數（7）或日期（2026-09-01）",

        # 筆記本（5.7）
        "还没有添加任何笔记本": "還未加入任何筆記本",
        "路径要从笔记本的名字开始，比如「{n}/…」":
            "路徑要由筆記本的名稱開始，例如「{n}/…」",
        "没有这个笔记本": "沒有這個筆記本",
        "没有叫「{n}」这个名字的笔记本": "沒有筆記本叫「{n}」",
        "这份不在任何一个笔记本里": "這一份不在任何一個筆記本裡面",
        "笔记本「{n}」现在找不到，它的文件夹可能被挪走了":
            "找不到筆記本「{n}」，它的資料夾可能被移走了",
        "这个文件夹已经是笔记本「{n}」了": "這個資料夾已經是筆記本「{n}」",
        "这个文件夹已经在「{n}」里面了": "這個資料夾已經在「{n}」裡面",
        "「{n}」就在这个文件夹里面": "「{n}」就在這個資料夾裡面",
        "至少要留一个笔记本": "最少要留一個筆記本",
        "笔记本得有个名字": "筆記本要有名稱",
        "名字里不能有斜杠": "名稱裡不能有斜線",
        "名字不能用「.」或「_」开头": "名稱不能用「.」或「_」開頭",
        "名字最多 {n} 个字": "名稱最多 {n} 個字",
        "已经有一个笔记本叫「{n}」了": "已經有一個筆記本叫「{n}」",
        "要一个文件夹的路径": "需要一個資料夾的路徑",
        "找不到这个文件夹：{p}": "找不到這個資料夾：{p}",
        "这是 AM·Note 自己的文件夹，不能当笔记本":
            "這是 AM·Note 自己的資料夾，不能當筆記本",
        "不认识这个颜色": "AM·Note 不認得這個顏色",

        # 接入嚮導（5.6）
        "不认识这个动作": "AM·Note 不認得這個動作",
        "缺一份模板：{p}": "缺少一份範本：{p}",
        "那个位置已经有一份文件了，先挪开": "那個位置已經有一份檔案，先移開",
        "命令行工具装好了：{p}": "命令列工具裝好了：{p}",
        "Skill 装好了：{p}": "Skill 裝好了：{p}",
        "AGENTS.md 写好了：{p}": "AGENTS.md 寫好了：{p}",
        "库地图导出好了，{n} 篇：{p}": "筆記庫地圖匯出好了，{n} 篇：{p}",
    },
}


def T(key, **kw):
    """查当前语言的译文，查不到回落到键本身，再套 `.format(**kw)`。

    返回 `Msg`——外面当普通字符串用，想要短码就读 `.code`。

    译文里的占位符写错了（比如漏了 `{e}`）不该把请求打挂：`format` 抛了就
    退回源串再套一次，还不行就原样给键。多余的关键字参数 `format` 自己会忽略，
    所以 `{g}` 这类只在部分语言里出现的占位符可以放心传。
    """
    table = MESSAGES.get(get_lang())
    text = (table or {}).get(key) or key
    if kw:
        try:
            text = text.format(**kw)
        except (KeyError, IndexError, ValueError):
            try:
                text = key.format(**kw)
            except (KeyError, IndexError, ValueError):
                text = key
    return Msg(text, CODES.get(key, ""))
