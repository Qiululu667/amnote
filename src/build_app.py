#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AM·Note · 打包成 macOS 应用  (v3, 2026-08-25)

把 `app_shell.m` 编成 `AM·Note.app`，那是唯一的日常入口：点开就起服务、开窗口。
Python 脚本和模板打进 bundle，app 可以放到任意位置。

用法：
    python3 build_app.py                  # 打包到仓库 dist/AM·Note.app
    python3 build_app.py --sign "证书名"   # 用自签证书签，权限能跨重新打包保留
    python3 build_app.py --dest DIR       # 指定输出目录，验收时先往临时目录丢
    python3 build_app.py --export-icons D # 顺带把 192/512 png 导到目录 D
"""

import os
import plistlib
import shutil
import subprocess
import sys
import urllib.parse

# PIL 只有内置画法 draw_icon() 要用。图标现在优先走 make_icon.py
# （母版 icon-master-1024.png，有 PIL 更好，没有就退 sips），
# 所以这个 import 不能是硬依赖——make_icon.py 没跑通才用到下面的画法。
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:                      # pragma: no cover
    Image = ImageDraw = ImageFont = None

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "AM·Note"
BUNDLE_ID = "app.amnote"

# 图标配色。底是象牙白——Dock 里深色图标扎堆，浅底反而认得出来；
# 而且小尺寸下深字压浅底比浅字压深底更耐缩，浅字容易糊成一团。
BG_TOP = (247, 244, 237)     # 象牙白，顶
BG_BOT = (236, 231, 220)     # 象牙白，底
FG = (24, 24, 28)            # 字标，墨
ACCENT = (208, 162, 82)      # 那一点，金

# 字标用的脸，按偏好排。**按字面名找，不按 .ttc 里的序号找**——
# 序号会随 macOS 版本变，写死序号的话哪天系统一升，图标就悄悄换了个字重。
ICON_FACES = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", ("Helvetica Neue", "Medium Italic")),
    ("/System/Library/Fonts/HelveticaNeue.ttc", ("Helvetica Neue", "Italic")),
    ("/System/Library/Fonts/HelveticaNeue.ttc", ("Helvetica Neue", "Medium")),
    ("/System/Library/Fonts/Avenir Next.ttc", ("Avenir Next", "Medium Italic")),
    ("/System/Library/Fonts/Avenir Next.ttc", ("Avenir Next", "Medium")),
]


def _icon_font(px):
    """按名字挑字标用的脸；名字都没命中就退到这几个文件里的第一张脸。"""
    if ImageFont is None:
        raise SystemExit("画不了图标：这台机器上没有 PIL，而且 make_icon.py 也没跑通。"
                         "装 pillow，或者把 make_icon.py 修好。")
    for path, want in ICON_FACES:
        if not os.path.exists(path):
            continue
        for i in range(24):
            try:
                f = ImageFont.truetype(path, px, index=i)
            except Exception:
                break
            if f.getname() == want:
                return f
    for path, _ in ICON_FACES:
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    raise SystemExit("画不了图标：系统里找不到 Helvetica Neue / Avenir Next。"
                     "换一款装在 /System/Library/Fonts 下的字体，改 ICON_FACES。")


def draw_icon(size=1024):
    """圆角象牙底 ＋ 斜体 AM 字标 ＋ 一点金。

    改过两版：三条横杠跟系统汉堡菜单撞（产品化方案第七节记过），
    「页＋书签」又跟一堆笔记类图标撞。字标撞不着，而且一眼知道是哪个。
    那一点金对应名字里的「·」，位置压在 A 和 M 交界的豁口上。

    字重必须是 Medium 一档：Light 好看，但 32px 以下细笔画直接消失。
    """
    if Image is None:
        raise SystemExit("画不了图标：这台机器上没有 PIL，而且 make_icon.py 也没跑通。"
                         "装 pillow，或者把 make_icon.py 修好。")
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    m = int(S * 0.098)                     # 边距，贴合 Big Sur 图标比例
    W = S - 2 * m
    r = int(W * 0.225)

    # 竖向渐变底
    grad = Image.new("RGBA", (1, S), (0, 0, 0, 255))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / max(1, S - 1)
        gd.point((0, y), fill=(
            int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
            int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
            int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t),
            255))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((m, m, S - m, S - m), radius=r, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    f = _icon_font(int(W * 0.48))

    # 两个字母分开画，中间收一点字距。PIL 没有 tracking，只能自己挪
    track = W * -0.02
    adv = [d.textlength(c, font=f) for c in "AM"]
    asc, desc = f.getmetrics()
    x = S / 2 - (sum(adv) + track) / 2
    y = S / 2 - (asc - desc) / 2 - W * 0.055      # 给底下那一点让出位置
    for i, c in enumerate("AM"):
        d.text((x, y), c, font=f, fill=FG + (255,))
        x += adv[i] + track

    rr = W * 0.052
    cy = S / 2 + W * 0.245
    d.ellipse((S / 2 - rr, cy - rr, S / 2 + rr, cy + rr), fill=ACCENT + (255,))
    return img


ICON_MAKER = "make_icon.py"


def make_icns(dest_icns):
    """图标进 bundle。

    优先跑同目录的 make_icon.py（图标线的产出，纯标准库），它会在 src/ 下生成
    icon.icns，这里只负责拷进 bundle。它不在、或者跑失败、或者没吐出 icon.icns，
    就退回本文件内置的 draw_icon() 那套自绘——两条线并行改造时谁先谁后都不卡住对方。
    返回实际用了哪条路，构建日志里会打出来。
    """
    src = os.path.join(HERE, ICON_MAKER)
    if os.path.exists(src):
        try:
            subprocess.run([sys.executable or "python3", src],
                           cwd=HERE, check=True, capture_output=True, timeout=300)
            produced = os.path.join(HERE, "icon.icns")
            if os.path.exists(produced):
                shutil.copy2(produced, dest_icns)
                return ICON_MAKER
            print(f"{ICON_MAKER} 跑完了但没有 icon.icns，退回内置画法")
        except Exception as e:
            detail = getattr(e, "stderr", b"") or b""
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", "replace")
            print(f"{ICON_MAKER} 没跑通（{type(e).__name__}: {e}），退回内置画法")
            if detail.strip():
                print("  ".join(("", *detail.strip().splitlines()[-5:])))
    _draw_icns(dest_icns)
    return "draw_icon()"


def _draw_icns(dest_icns):
    """内置自绘图标。make_icon.py 交付之前的现行画法，也是它出问题时的退路。"""
    iconset = os.path.join(HERE, "_icon.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    base = draw_icon(1024)
    for px in (16, 32, 128, 256, 512):
        base.resize((px, px), Image.LANCZOS).save(
            os.path.join(iconset, f"icon_{px}x{px}.png"))
        base.resize((px * 2, px * 2), Image.LANCZOS).save(
            os.path.join(iconset, f"icon_{px}x{px}@2x.png"))
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", dest_icns], check=True)
    shutil.rmtree(iconset, ignore_errors=True)


def export_icons(dest_dir):
    """给 A 线的 manifest 用。图标画法只有这一份，不要另画。"""
    os.makedirs(dest_dir, exist_ok=True)
    base = draw_icon(1024)
    out = []
    for px in (192, 512):
        p = os.path.join(dest_dir, f"icon-{px}.png")
        base.resize((px, px), Image.LANCZOS).save(p)
        out.append(p)
    return out


# ───────────────────────── 后台服务脚本（LaunchAgent 调用）─────────────────────────
NATIVE_SRC = "app_shell.m"
# 5.6.1 = 库内 html 改成带 allow-scripts 的沙箱 iframe，壳这边补上「只认主框」两道闸：
# amn 通道（didReceiveScriptMessage:）丢掉非主框的消息；导航策略里非主框的外链只有用户
# 真点了（LinkActivated）才交给系统浏览器，脚本改 location.href 那种一律掐掉。
# LinkActivated 本身不作数（子框里 a.click() 报的也是它），子框外链另要求一秒内有过真实
# 键鼠输入——本地 NSEvent 监视器记时刻，合成事件伪造不出 NSEvent。四个面板 delegate
# （alert / confirm / prompt / openPanel）同样加了非主框守卫，非主框静默当取消。
# 新增只读启动键 `-AMNOpenAtLaunch <库内相对路径>`（验收用，走 queueOpen 那条队列）。
# 5.6.0 = 个人资料（问候、头像、设置「个人」）+ Agent 协作（amnote CLI / MCP / skill、
# 库地图、/__search 扩参、Agent 署名）；小云挪到左上当回首页键。
# 5.5.0 = 界面多语言（简体中文 / 繁體中文（香港）/ English，可跟随系统）：壳的菜单、
# 弹框、欢迎笔记走 app_shell_i18n.h 的词典，网页走 locales/*.js，服务端消息走
# portal_i18n.py；新注入 __AMN_LANG__ / __AMN_LANG_PREF__，新桥接 setLanguage。
# 阅读字体新增苹方-港（六档字重）和新细明体。
# 5.4.1 = 开始页文件夹芯片行可收起，状态记在 tp-state.chipsOpen，设置里有开关。
# 5.4.0 = 珍珠紫玻璃图标；首启不再弹「选择文件夹 / 退出」，没库时服务起在占位空根、
# 欢迎与「找不到库」由网页画；新注入 __AMN_SHELL_VERSION__ / __AMN_VAULT_STATE__ /
# __AMN_VAULT_PATH__ / __AMN_ONBOARDING__ / __AMN_AUTO_UPDATE__；新桥接 chooseVault /
# createVault / checkUpdate / setAutoUpdate / openURL / revealVault；顶栏 42+48，
# kChromeH 52→90；显示菜单去掉书签栏，⌘L 改名「搜索」。
# 5.2.0 = 浏览器壳界面：地址栏搜库、书签栏是顶层文件夹、新标签是随手记。
# 5.1.1 = 云朵便签图标（20260904）。母版 src/icon-master-1024.png。
# 5.1.0 = GitHub Releases 检查更新：菜单「检查更新…」，可下载 zip 替换当前 .app
# （20260903）。有 digest 核 sha256，解开后核对 bundle id。
# 5.0.0 = 开源首发。
# 版本号是唯一能在「关于 AM·Note」里看出来跑的是新壳还是旧壳的地方，改了壳就要动它。
NATIVE_VERSION = ("5.6.1", "31")
MIN_MACOS = "12.0"


def compile_native(dest_exe):
    """把 app_shell.m 编成 Contents/MacOS 下的二进制。

    （UserNotifications 那一条 2026-08-25 去掉了：通知是给收件箱未读数用的，那一屏撤了。）
    -mmacosx-version-min 跟 Info.plist 的 LSMinimumSystemVersion 对齐，
    免得 plist 写着 12.0、二进制却按本机版本编，拿到旧机器上直接起不来。
    """
    src = os.path.join(HERE, NATIVE_SRC)
    if not os.path.exists(src):
        raise SystemExit(f"找不到 {src}：原生壳的源码没了，恢复 {NATIVE_SRC} 再打包。")
    # -I HERE：app_shell.m 里 `#import "app_shell_i18n.h"` 找的是同目录那份。
    # 引号形式本来就先看源文件所在目录，这条是明写一遍，免得以后换调用方式踩空。
    subprocess.run(
        ["clang", "-fobjc-arc", "-fmodules", "-Wall", "-O2",
         f"-mmacosx-version-min={MIN_MACOS}",
         "-I", HERE,
         "-framework", "AppKit", "-framework", "WebKit",
         src, "-o", dest_exe],
        check=True)


RESOURCE_FILES = (
    "portal_server.py",
    "portal_i18n.py",
    "fulltext.py",
    "amnote_cli.py",
    "template.html",
    "icon-192.png",
    "icon-512.png",
)

# 命令行包装脚本。**可执行位要留住**：`~/.local/bin/amnote` 是一条指到它的
# 符号链接（/__agent_setup 的 cli 动作建的），丢了 +x 那条链接就跑不起来。
# shutil.copy2 在这台机器上留得住权限位，但目标已存在时不一定——所以再 chmod 一次。
EXEC_RESOURCE_FILES = (
    "amnote",
)

# 给 Agent 的两份模板。服务端 /__agent_setup 从这儿读，把 {{AMNOTE_BIN}}
# 换成 app 里 amnote 的绝对路径再写到用户家目录 / 库根。
AGENT_DIR = "agent"
AGENT_FILES = (
    "SKILL.md",
    "AGENTS.md",
)

# 网页词典。整个 src/locales/ 拷成 Contents/Resources/locales/，门户按
# /__i18n/<lang>.js 出它们；少一份都当构建失败——装出来的 app 会缺一种语言。
LOCALES_DIR = "locales"
LOCALE_FILES = (
    "en.js",
    "zh-HK.js",
)

# app 自己的本地化资源目录。里面只有 InfoPlist.strings（文档类型名），但**目录必须在**：
# CFBundleLocalizations 之外，AppKit 还要靠 .lproj 判断这个 app 支持哪几种语言，
# 没有它系统自带的菜单项（服务、隐藏、编辑里的听写…）和保存面板不会跟着切。
BUNDLE_LOCALIZATIONS = ("en", "zh-Hans", "zh-Hant", "zh-HK")

# 文档类型名。Info.plist 里写的是简体源串，各语言在 InfoPlist.strings 里覆盖。
INFOPLIST_STRINGS = {
    "en":      {"Markdown 文稿": "Markdown Document", "网页": "Web Page"},
    "zh-Hans": {"Markdown 文稿": "Markdown 文稿",     "网页": "网页"},
    "zh-Hant": {"Markdown 文稿": "Markdown 文件",     "网页": "網頁"},
    "zh-HK":   {"Markdown 文稿": "Markdown 文件",     "网页": "網頁"},
}

# 菜单栏小云由 make_icon.py 从母版抠出。抠失败时壳退到 SF cloud.fill，不挡打包。
OPTIONAL_RESOURCE_FILES = (
    "menubar-cloud.png",
    "menubar-cloud@2x.png",
)


def copy_resources(res_dir):
    """把 Python 服务、模板、词典和说明打进 Contents/Resources/，app 才能放到任意位置。"""
    missing = []
    for name in RESOURCE_FILES:
        src = os.path.join(HERE, name)
        if not os.path.exists(src):
            missing.append(src)
            continue
        shutil.copy2(src, os.path.join(res_dir, name))
    for name in OPTIONAL_RESOURCE_FILES:
        src = os.path.join(HERE, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(res_dir, name))

    for name in EXEC_RESOURCE_FILES:
        src = os.path.join(HERE, name)
        if not os.path.exists(src):
            missing.append(src)
            continue
        dst = os.path.join(res_dir, name)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)

    # agent/ 整个目录拷过去，形状跟源码一样（Resources/agent/SKILL.md）。
    src_agent = os.path.join(HERE, AGENT_DIR)
    dst_agent = os.path.join(res_dir, AGENT_DIR)
    if not os.path.isdir(src_agent):
        missing.append(src_agent)
    else:
        os.makedirs(dst_agent, exist_ok=True)
        for name in AGENT_FILES:
            one = os.path.join(src_agent, name)
            if not os.path.exists(one):
                missing.append(one)
                continue
            shutil.copy2(one, os.path.join(dst_agent, name))

    # 网页词典整目录拷过去。先点名核对必需的那几份，再把目录里其余的 .js 一并带上
    # （以后加语言只要往 src/locales/ 里丢文件，这里不用改）。
    src_loc = os.path.join(HERE, LOCALES_DIR)
    dst_loc = os.path.join(res_dir, LOCALES_DIR)
    if not os.path.isdir(src_loc):
        missing.append(src_loc)
    else:
        os.makedirs(dst_loc, exist_ok=True)
        for name in LOCALE_FILES:
            if not os.path.exists(os.path.join(src_loc, name)):
                missing.append(os.path.join(src_loc, name))
        for name in sorted(os.listdir(src_loc)):
            one = os.path.join(src_loc, name)
            if os.path.isfile(one) and not name.startswith("."):
                shutil.copy2(one, os.path.join(dst_loc, name))

    readme = os.path.join(HERE, "..", "README.md")
    if os.path.exists(readme):
        shutil.copy2(readme, os.path.join(res_dir, "README.md"))
    if missing:
        raise SystemExit("找不到这些文件，无法打进 app：\n" + "\n".join(missing))


def write_lprojs(res_dir):
    """建空的 .lproj 并写 InfoPlist.strings。

    .lproj 目录本身就是信号：没有它，AppKit 认为这个 app 只有开发语言那一种，
    自带的菜单项和系统面板不会跟着 AppleLanguages 走。
    InfoPlist.strings 只翻文档类型名（访达「显示简介」里那一行）。
    """
    for lang in BUNDLE_LOCALIZATIONS:
        d = os.path.join(res_dir, f"{lang}.lproj")
        os.makedirs(d, exist_ok=True)
        table = INFOPLIST_STRINGS.get(lang)
        if not table:
            continue
        lines = ['/* AM·Note · 文档类型名。键 = Info.plist 里的简体源串。 */']
        for k, v in table.items():
            lines.append('"%s" = "%s";' % (k, v))
        with open(os.path.join(d, "InfoPlist.strings"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def codesign_app(app, identity="-"):
    """
    签名。默认 ad-hoc（identity="-"），因为钥匙串里现在一个可用身份都没有
    （`security find-identity -v -p codesigning` 返回 0 valid identities）。

    ad-hoc 的代价：TCC 授权是按 cdhash 记的，重新编译 cdhash 就变，桌面读取权限
    要重给一次。想让授权跨重新打包保留，得在钥匙串访问里自建一张「代码签名」证书，
    然后 `python3 build_app.py --native --sign "证书名"`——那种情况 TCC 按签名身份
    ＋ bundle id 记，重编不掉权限。
    """
    subprocess.run(["codesign", "--force", "--sign", identity, app], check=True)


def build_native(dest_dir, identity="-"):
    app = os.path.join(dest_dir, f"{APP_NAME}.app")
    shutil.rmtree(app, ignore_errors=True)
    macos = os.path.join(app, "Contents", "MacOS")
    res = os.path.join(app, "Contents", "Resources")
    os.makedirs(macos)
    os.makedirs(res)

    icon_from = make_icns(os.path.join(res, "icon.icns"))
    compile_native(os.path.join(macos, APP_NAME))
    copy_resources(res)
    write_lprojs(res)

    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleExecutable": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleIconFile": "icon",
            # 多语言（5.5）。开发语言写 en：源码里的字面量是简体，但简体、繁体（香港）
            # 和英文三份界面文案都在 app 自己的词典里，系统只需要知道「支持这几种」。
            # 少了 CFBundleLocalizations，系统自带的面板一律回落到开发语言。
            "CFBundleDevelopmentRegion": "en",
            "CFBundleLocalizations": list(BUNDLE_LOCALIZATIONS),
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": NATIVE_VERSION[0],
            "CFBundleVersion": NATIVE_VERSION[1],
            "LSMinimumSystemVersion": MIN_MACOS,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            # WKWebView 载 http://127.0.0.1 是明文。ATS 不放行的话窗口就是一片空白，
            # 而且不报任何错——漏了这一条会白查半天。
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            # 两条都必须是 false：这个 app 名下挂着 portal_server.py 子进程，
            # 系统自作主张把它挂起或直接杀掉，服务就断了。
            "NSSupportsAutomaticTermination": False,
            "NSSupportsSuddenTermination": False,
            # N-10：访达双击 md 用 AM·Note 打开。html 仍是 Alternate，不抢浏览器。
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Markdown 文稿",
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Owner",
                    "CFBundleTypeExtensions": ["md", "markdown", "mdown", "mdwn"],
                    "LSItemContentTypes": ["net.daringfireball.markdown"],
                },
                {
                    "CFBundleTypeName": "网页",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": ["public.html"],
                },
            ],
            "UTImportedTypeDeclarations": [
                {
                    "UTTypeIdentifier": "net.daringfireball.markdown",
                    "UTTypeDescription": "Markdown 文稿",
                    "UTTypeConformsTo": ["public.plain-text"],
                    "UTTypeTagSpecification": {
                        "public.filename-extension": ["md", "markdown", "mdown", "mdwn"],
                        "public.mime-type": ["text/markdown", "text/x-markdown"],
                    },
                },
            ],
            # N-11：amnote://open?path=… 让 Agent 和别的工具直接唤起并定位到某一份
            "CFBundleURLTypes": [
                {
                    "CFBundleURLName": BUNDLE_ID,
                    "CFBundleTypeRole": "Viewer",
                    "CFBundleURLSchemes": ["amnote"],
                },
            ],
            "NSHumanReadableCopyright": "AM·Note contributors",
        }, f)

    # 签名必须排在所有内容写完之后（它要生成 Contents/_CodeSignature/），touch 再排在签名之后
    codesign_app(app, identity)
    subprocess.run(["touch", app], check=False)
    print(f"图标来源：{icon_from}")
    return app


if __name__ == "__main__":
    if "--export-icons" in sys.argv:
        d = sys.argv[sys.argv.index("--export-icons") + 1]
        for p in export_icons(d):
            print(f"已导出 {p}")
        sys.exit(0)

    if "--dest" in sys.argv:
        dest = sys.argv[sys.argv.index("--dest") + 1]
    else:
        dest = os.path.abspath(os.path.join(HERE, "..", "dist"))

    ident = (sys.argv[sys.argv.index("--sign") + 1] if "--sign" in sys.argv else "-")
    p = build_native(dest, ident)
    print(f"已生成 {p}")
    print("这是日常入口：点开就起服务、开窗口。")
    print("关窗口只关窗口，服务和菜单栏图标留着（双击一份 md 会把窗口弹回来）；")
    print("要连服务一起收掉，走 ⌘Q 或菜单栏图标里的「退出」。")
    print("旧实例还开着的话，先退掉再点新的——不然新旧两个壳会抢同一个服务。")
