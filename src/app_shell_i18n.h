// AM·Note · 原生壳的界面词典（5.5 多语言）
//
// ─── 规矩（跟网页 src/locales/*.js 同一套）─────────────────────────────
//
// · **键 = 简体中文源串本身**。zh-Hans 不需要词典：查不到就回落到键，
//   所以源码里 `L(@"打开窗口")` 在简体下原样显示。
// · 同一源串在不同语境要不同译文时，键加 `##语境` 后缀（如 `搜索##menu`），
//   回落时把 `##…` 去掉再显示。壳这边当前一条都不需要，机制先留着。
// · **格式串三种语言的参数个数与顺序必须一致**（`%@` `%ld` `%.1f` `%llu` `%d`）。
//   顺序换不了的话就用 `%1$@` 位置参数——现在还没有这种情况。
// · 英文用半角冒号、括号、引号（`Port: %@`），中文用全角「」（）：。
// · 产品名 AM·Note、快捷键符号（⌘ ⌥ ⇧ ⌃ ⌫ ⤢）任何语言都不翻。
//
// 语言解析（契约 §1 §2）：
//   NSUserDefaults["AMNLanguage"]（非 auto）→ 按规则映射 preferredLanguages[0]。
//   `-AMNLanguage en` 走 NSArgumentDomain 自动生效，跟 -AMNVaultPath 同理。
//   解析结果缓存在 gAMNLang 里，改偏好之后要调 AMNResetLanguage()。

#ifndef AMN_APP_SHELL_I18N_H
#define AMN_APP_SHELL_I18N_H

#import <Foundation/Foundation.h>

/// 界面语言偏好。值域 auto / zh-Hans / zh-HK / en。
static NSString *const kAMNLangKey = @"AMNLanguage";

/// 偏好值归一化。认不出来返回 nil（页面发来的脏值就地丢掉）。
static inline NSString *AMNNormalizeLanguagePref(id raw) {
    if (![raw isKindOfClass:NSString.class]) return nil;
    NSString *t = [(NSString *)raw lowercaseString];
    if ([t isEqualToString:@"auto"])    return @"auto";
    if ([t isEqualToString:@"zh-hans"]) return @"zh-Hans";
    if ([t isEqualToString:@"zh-hk"])   return @"zh-HK";
    if ([t isEqualToString:@"en"])      return @"en";
    return nil;
}

/// 契约 §1：系统语言标识 → 界面语言代号。壳和网页必须一致。
static inline NSString *AMNMapLanguageTag(NSString *tag) {
    NSString *t = tag.lowercaseString;
    if (!t.length) return @"en";
    if ([t hasPrefix:@"zh-hans"]) return @"zh-Hans";
    if ([t isEqualToString:@"zh"] || [t isEqualToString:@"zh-cn"] ||
        [t isEqualToString:@"zh-sg"]) return @"zh-Hans";
    if ([t hasPrefix:@"zh-hant"] || [t hasPrefix:@"zh-hk"] ||
        [t hasPrefix:@"zh-tw"]   || [t hasPrefix:@"zh-mo"] ||
        [t hasPrefix:@"yue"]) return @"zh-HK";
    return @"en";
}

/// 用户偏好原值。没写过（或写坏了）＝ auto。
static inline NSString *AMNLanguagePref(void) {
    NSString *v = AMNNormalizeLanguagePref(
        [NSUserDefaults.standardUserDefaults stringForKey:kAMNLangKey]);
    return v ?: @"auto";
}

/// 非 auto 时写进本 app 域的 AppleLanguages，让 AppKit 自带的面板跟着走。
static inline NSString *AMNAppleLanguageTag(NSString *lang) {
    if ([lang isEqualToString:@"zh-HK"])   return @"zh-Hant-HK";
    if ([lang isEqualToString:@"zh-Hans"]) return @"zh-Hans";
    return @"en";
}

static NSString *gAMNLang = nil;

/// 已解析的生效语言：zh-Hans / zh-HK / en。
static inline NSString *AMNLanguage(void) {
    if (gAMNLang) return gAMNLang;
    NSString *pref = AMNLanguagePref();
    if (![pref isEqualToString:@"auto"]) { gAMNLang = pref; return gAMNLang; }
    gAMNLang = AMNMapLanguageTag(NSLocale.preferredLanguages.firstObject);
    return gAMNLang;
}

/// 偏好改了之后清缓存，下一次 AMNLanguage() 重算。
static inline void AMNResetLanguage(void) { gAMNLang = nil; }

/// 回落时去掉 `##语境` 后缀。
static inline NSString *AMNStripContext(NSString *key) {
    NSRange r = [key rangeOfString:@"##"];
    return r.location == NSNotFound ? key : [key substringToIndex:r.location];
}

// ───────────────────────── English ─────────────────────────

static inline NSDictionary<NSString *, NSString *> *AMNDictEN(void) {
    static NSDictionary *d = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        d = @{
        // ── 起服务 / 出错 ──
        @"这台电脑缺 Python 运行环境\n\n"
         "Python 随 Xcode 命令行工具提供。在终端运行\n\n"
         "    xcode-select --install\n\n"
         "装好后重新打开 AM·Note。\n\n试过这几个位置：\n%@":
            @"Python isn't available on this Mac\n\n"
             "Python comes with the Xcode Command Line Tools. In Terminal, run\n\n"
             "    xcode-select --install\n\n"
             "then open AM·Note again.\n\nLocations tried:\n%@",
        @"还没有选择文件夹": @"No folder chosen yet",
        @"python3 启动失败\n\n%@\n%@": @"Couldn't start python3\n\n%@\n%@",
        @"服务 %.0f 秒内没有报出端口号\n\n%@":
            @"The service didn't report a port within %.0f seconds\n\n%@",
        @"服务启动即退出（退出码 %d）\n\n%@":
            @"The service quit right after starting (exit code %d)\n\n%@",
        @"（服务没有输出任何错误信息）": @"(The service printed no error output.)",
        @"找不到程序文件，请重新编译或从发布包安装":
            @"Program files are missing — rebuild, or install from a release package",
        @"建不出笔记库目录": @"Couldn't create the vault folder",
        @"请用菜单「库 → 选择文件夹…」选一个可写的文件夹。":
            @"Use Vault → Choose Folder… and pick a folder you can write to.",
        @"端口号不对": @"Bad port number",
        @"服务报回来的端口是 %ld。": @"The service reported port %ld.",
        @"未知错误": @"Unknown error",
        @"AM·Note 起不来": @"AM·Note couldn't start",
        @"重试": @"Try Again",
        @"拷贝错误信息": @"Copy Error Details",
        @"退出": @"Quit",
        @"门户加载失败": @"The portal failed to load",
        @"连不上门户服务": @"Can't reach the portal service",
        @"%@\n\n端口 %ld\n\n%@": @"%@\n\nPort %ld\n\n%@",

        // ── 选库 / 建库 ──
        @"选择文件夹": @"Choose Folder",
        @"AM·Note 会索引你选的文件夹。文件留在原地，不会上传到网上。":
            @"AM·Note will index the folder you choose. Your files stay where they are "
             "and are never uploaded.",
        @"换一个文件夹。AM·Note 只索引你选的这一个，文件都留在原地。":
            @"Pick a different folder. AM·Note indexes only the one you choose, "
             "and your files stay where they are.",
        @"这个位置不对": @"That location won't work",
        @"建不出这个文件夹": @"Couldn't create that folder",

        // ── 通用按钮 ──
        @"好": @"OK",
        @"取消": @"Cancel",
        @"确认": @"Confirm",
        @"拷贝": @"Copy",

        // ── 状态栏菜单 ──
        @"打开窗口": @"Open Window",
        @"检查更新…": @"Check for Updates…",
        @"重扫全库": @"Rescan Vault",
        @"服务信息": @"Service Info",
        @"退出 AM·Note": @"Quit AM·Note",

        // ── 查找栏 ──
        @"在本页查找": @"Find on Page",
        @"上一个": @"Previous",
        @"下一个": @"Next",
        @"完成": @"Done",
        @"没找到": @"Not found",

        // ── 工具栏（现在不挂窗口，留着不删）──
        @"新建随手记": @"New Quick Note",
        @"搜索": @"Search",
        @"搜索全库": @"Search the vault",

        // ── 主菜单 · 应用 ──
        @"关于 AM·Note": @"About AM·Note",
        @"自动检查更新": @"Check for Updates Automatically",
        @"设置…": @"Settings…",
        @"服务": @"Services",
        @"隐藏 AM·Note": @"Hide AM·Note",
        @"隐藏其他": @"Hide Others",
        @"全部显示": @"Show All",

        // ── 主菜单 · 文件 ──
        @"文件": @"File",
        @"新建窗口": @"New Window",
        @"新建标签页": @"New Tab",
        @"在新窗口打开": @"Open in New Window",
        @"在访达中显示": @"Reveal in Finder",
        @"拷贝路径": @"Copy Path",
        @"分享…": @"Share…",
        @"移到废纸篓": @"Move to Trash",
        @"进入编辑": @"Start Editing",
        @"存储": @"Save",
        @"关闭": @"Close",
        @"关闭窗口": @"Close Window",
        @"关闭标签": @"Close Tab",
        @"打印…": @"Print…",

        // ── 主菜单 · 编辑 ──
        @"编辑": @"Edit",
        @"撤销": @"Undo",
        @"重做": @"Redo",
        @"剪切": @"Cut",
        @"粘贴": @"Paste",
        @"全选": @"Select All",
        @"快速直达": @"Quick Open",
        @"搜索正文": @"Search Text",

        // ── 主菜单 · 显示 ──
        @"显示": @"View",
        @"后退": @"Back",
        @"前进": @"Forward",
        @"显示大纲": @"Show Outline",
        @"随手记": @"Quick Notes",
        @"最近": @"Recent",
        @"重新载入": @"Reload",
        @"放大": @"Zoom In",
        @"缩小": @"Zoom Out",
        @"实际大小": @"Actual Size",
        @"进入全屏": @"Enter Full Screen",

        // ── 主菜单 · 库 ──
        @"库": @"Vault",
        @"选择文件夹…": @"Choose Folder…",
        @"在浏览器里打开门户": @"Open Portal in Browser",
        @"打开工具目录": @"Open Tools Folder",

        // ── 主菜单 · 窗口 / 帮助 ──
        @"窗口": @"Window",
        @"最小化": @"Minimize",
        @"缩放": @"Zoom",
        @"前置全部窗口": @"Bring All to Front",
        @"帮助": @"Help",
        @"AM·Note 帮助": @"AM·Note Help",
        @"快捷键一览": @"Keyboard Shortcuts",

        // ── 服务信息 ──
        @"端口：%@\n进程：%@\n窗口：%@\n工具目录：%@\n解释器：%@":
            @"Port: %@\nProcess: %@\nWindow: %@\nTools folder: %@\nInterpreter: %@",
        @"还没起来": @"Not started yet",
        @"未启动": @"Not running",
        @"复用外部实例": @"Reusing an external instance",
        @"开着": @"Open",
        @"关着（服务常驻，双击一份 md 会自己弹回来）":
            @"Closed (the service keeps running; double-click a .md to bring it back)",
        @"未找到": @"Not found",
        @"无": @"None",

        // ── 快捷键一览 ──
        @"快捷键": @"Keyboard Shortcuts",
        @"⌘T 新建标签页（开始页）\n⌘N 新建窗口\n⌘L 搜索\n"
         "⌘[ / ⌘] 后退 / 前进\n⌥⌘C 拷贝路径\n⇧⌘R 在访达中显示\n⌥⌘O 把这份放到独立阅读窗\n"
         "⌘E 进入编辑\n⌘S 存储\n⌘⌫ 移到废纸篓\n⌘W 关闭标签／仅剩起始页时关窗口\n"
         "⇧⌘W 关闭窗口\n⌘P 打印\n"
         "⌃Tab 切换标签\n"
         "⌘K 快速直达\n⌥⌘K 搜索正文\n⌥⌘I 显示大纲\n"
         "⌘F 在本页查找\n⌘R 重新载入\n"
         "⌃⌘F 进入全屏\nEsc 关浮层 / 退出编辑":
            @"⌘T New Tab (Start page)\n⌘N New Window\n⌘L Search\n"
             "⌘[ / ⌘] Back / Forward\n⌥⌘C Copy Path\n⇧⌘R Reveal in Finder\n"
             "⌥⌘O Open in New Window\n"
             "⌘E Start Editing\n⌘S Save\n⌘⌫ Move to Trash\n"
             "⌘W Close Tab (closes the window when only the Start page is left)\n"
             "⇧⌘W Close Window\n⌘P Print\n"
             "⌃Tab Switch tabs\n"
             "⌘K Quick Open\n⌥⌘K Search Text\n⌥⌘I Show Outline\n"
             "⌘F Find on Page\n⌘R Reload\n"
             "⌃⌘F Enter Full Screen\nEsc Dismiss overlay / leave editing",

        // ── 未保存改动 ──
        @"有改动还没保存": @"You have unsaved changes",
        @"关掉这个窗口，正在编辑的改动会丢掉。":
            @"Closing this window will discard the changes you're editing.",
        @"回去保存": @"Go Back",
        @"直接关闭": @"Close Anyway",
        @"关掉窗口会把门户卸下来，正在编辑的稿子会丢掉改动。服务和状态栏图标照常留着。":
            @"Closing the window unloads the portal, so the note you're editing will lose "
             "its changes. The service and the menu bar icon stay.",
        @"安装更新要退出 AM·Note。未保存的改动会丢掉。":
            @"Installing the update quits AM·Note. Unsaved changes will be lost.",
        @"放弃改动并更新": @"Discard Changes and Update",
        @"独立窗口里正在编辑的稿子有未保存的改动，现在退出会丢掉。":
            @"A separate window has unsaved changes. Quitting now will discard them.",
        @"直接退出": @"Quit Anyway",
        @"门户里正在编辑的稿子有未保存的改动，现在退出会丢掉。":
            @"The portal has unsaved changes. Quitting now will discard them.",

        // ── 更新 ──
        @"连不上 GitHub。可以一会儿再试，或到仓库 Releases 手动下载。":
            @"Can't reach GitHub. Try again later, or download it from the repository's "
             "Releases page.",
        @"GitHub 上这个版本没有 mac 安装包（AMNote-mac.zip）。":
            @"This release has no Mac package (AMNote-mac.zip) on GitHub.",
        @"已是最新版本": @"You're up to date",
        @"当前是 %@。": @"You're running %@.",
        @"现在检查不了更新": @"Can't check for updates right now",
        @"连不上 GitHub。": @"Can't reach GitHub.",
        @"打开下载页": @"Open Download Page",
        @"现在是 %@。安装会替换当前的 AM·Note，装完自动打开。\n笔记还在原来的文件夹里，不会被动。":
            @"You're running %@. Installing replaces the current AM·Note and opens it when "
             "it's done.\nYour notes stay in the same folder and aren't touched.",
        @"有新版本 %@": @"Version %@ is available",
        @"安装更新": @"Install Update",
        @"稍后": @"Later",
        @"跳过此版本": @"Skip This Version",
        @"更新 AM·Note": @"Update AM·Note",
        @"正在下载…": @"Downloading…",
        @"下载地址不是 GitHub，已中止。":
            @"The download address isn't on GitHub, so it was stopped.",
        @"正在下载 %@…": @"Downloading %@…",
        @"正在下载 %@…  %.1f / %.1f MB": @"Downloading %@…  %.1f / %.1f MB",
        @"下载失败。": @"Download failed.",
        @"下载失败（HTTP %ld）。": @"Download failed (HTTP %ld).",
        @"建临时目录失败。": @"Couldn't create a temporary folder.",
        @"保存安装包失败。": @"Couldn't save the downloaded package.",
        @"正在校验…": @"Verifying…",
        @"安装包大小不对（%llu，期望 %@）。":
            @"The package is the wrong size (%llu, expected %@).",
        @"不认识的校验算法：%@。": @"Unknown checksum algorithm: %@.",
        @"算不了安装包的校验值。": @"Couldn't compute the package checksum.",
        @"安装包校验失败，没有安装。请到 GitHub Releases 重新下载。":
            @"The package failed verification and wasn't installed. Download it again from "
             "GitHub Releases.",
        @"解压失败。": @"Couldn't unarchive the package.",
        @"解压安装包失败。": @"Unarchiving the package failed.",
        @"压缩包里没有 AM·Note.app。": @"The archive doesn't contain AM·Note.app.",
        @"安装包的 Bundle ID 不是 app.amnote，已中止。":
            @"The package's bundle ID isn't app.amnote, so it was stopped.",
        @"包里的版本是 %@，不比现在的新。":
            @"The package contains version %@, which isn't newer than the current one.",
        @"包里的版本是 %@，比 GitHub 上的 %@ 还旧。":
            @"The package contains version %@, which is older than %@ on GitHub.",
        @"安装包里缺少可执行文件。": @"The package is missing its executable.",
        @"挪新版本失败。": @"Couldn't move the new version into place.",
        @"当前不是从 .app 运行的，没法自动替换。请到 GitHub 手动下载。":
            @"AM·Note isn't running from a .app bundle, so it can't replace itself. "
             "Download the update from GitHub.",
        @"找不到解好的新版本。": @"The unpacked new version is missing.",
        @"写更新脚本失败。": @"Couldn't write the updater script.",
        @"拉不起更新脚本。": @"Couldn't launch the updater script.",
        };
    });
    return d;
}

// ───────────────────────── 繁體中文（香港）─────────────────────────

static inline NSDictionary<NSString *, NSString *> *AMNDictHK(void) {
    static NSDictionary *d = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        d = @{
        // ── 起服务 / 出错 ──
        @"这台电脑缺 Python 运行环境\n\n"
         "Python 随 Xcode 命令行工具提供。在终端运行\n\n"
         "    xcode-select --install\n\n"
         "装好后重新打开 AM·Note。\n\n试过这几个位置：\n%@":
            @"這部電腦缺少 Python 執行環境\n\n"
             "Python 隨 Xcode 命令列工具提供。在「終端機」執行\n\n"
             "    xcode-select --install\n\n"
             "裝好後重新打開 AM·Note。\n\n已試過這幾個位置：\n%@",
        @"还没有选择文件夹": @"還沒有選擇資料夾",
        @"python3 启动失败\n\n%@\n%@": @"python3 啟動失敗\n\n%@\n%@",
        @"服务 %.0f 秒内没有报出端口号\n\n%@":
            @"服務在 %.0f 秒內沒有報出連接埠\n\n%@",
        @"服务启动即退出（退出码 %d）\n\n%@":
            @"服務啟動後隨即結束（結束代碼 %d）\n\n%@",
        @"（服务没有输出任何错误信息）": @"（服務沒有輸出任何錯誤資訊）",
        @"找不到程序文件，请重新编译或从发布包安装":
            @"找不到程式檔案，請重新編譯或從發佈套件安裝",
        @"建不出笔记库目录": @"無法建立筆記庫資料夾",
        @"请用菜单「库 → 选择文件夹…」选一个可写的文件夹。":
            @"請用選單「筆記庫 → 選擇資料夾…」選一個可寫入的資料夾。",
        @"端口号不对": @"連接埠不正確",
        @"服务报回来的端口是 %ld。": @"服務報回來的連接埠是 %ld。",
        @"未知错误": @"未知錯誤",
        @"AM·Note 起不来": @"AM·Note 無法啟動",
        @"重试": @"再試一次",
        @"拷贝错误信息": @"拷貝錯誤資訊",
        @"退出": @"結束",
        @"门户加载失败": @"門戶載入失敗",
        @"连不上门户服务": @"連不上門戶服務",
        @"%@\n\n端口 %ld\n\n%@": @"%@\n\n連接埠 %ld\n\n%@",

        // ── 选库 / 建库 ──
        @"选择文件夹": @"選擇資料夾",
        @"AM·Note 会索引你选的文件夹。文件留在原地，不会上传到网上。":
            @"AM·Note 會為你選的資料夾建立索引。檔案留在原處，不會上載到網上。",
        @"换一个文件夹。AM·Note 只索引你选的这一个，文件都留在原地。":
            @"換一個資料夾。AM·Note 只為你選的這一個建立索引，檔案都留在原處。",
        @"这个位置不对": @"這個位置不正確",
        @"建不出这个文件夹": @"無法建立這個資料夾",

        // ── 通用按钮 ──
        @"好": @"好",
        @"取消": @"取消",
        @"确认": @"確認",
        @"拷贝": @"拷貝",

        // ── 状态栏菜单 ──
        @"打开窗口": @"打開視窗",
        @"检查更新…": @"檢查更新…",
        @"重扫全库": @"重新掃描筆記庫",
        @"服务信息": @"服務資訊",
        @"退出 AM·Note": @"結束 AM·Note",

        // ── 查找栏 ──
        @"在本页查找": @"在本頁尋找",
        @"上一个": @"上一個",
        @"下一个": @"下一個",
        @"完成": @"完成",
        @"没找到": @"找不到",

        // ── 工具栏 ──
        @"新建随手记": @"新增隨手記",
        @"搜索": @"搜尋",
        @"搜索全库": @"搜尋整個筆記庫",

        // ── 主菜单 · 应用 ──
        @"关于 AM·Note": @"關於 AM·Note",
        @"自动检查更新": @"自動檢查更新",
        @"设置…": @"設定…",
        @"服务": @"服務",
        @"隐藏 AM·Note": @"隱藏 AM·Note",
        @"隐藏其他": @"隱藏其他",
        @"全部显示": @"全部顯示",

        // ── 主菜单 · 文件 ──
        @"文件": @"檔案",
        @"新建窗口": @"新增視窗",
        @"新建标签页": @"新增分頁",
        @"在新窗口打开": @"在新視窗中打開",
        @"在访达中显示": @"在 Finder 中顯示",
        @"拷贝路径": @"拷貝路徑",
        @"分享…": @"分享…",
        @"移到废纸篓": @"移到垃圾桶",
        @"进入编辑": @"進入編輯",
        @"存储": @"儲存",
        @"关闭": @"關閉",
        @"关闭窗口": @"關閉視窗",
        @"关闭标签": @"關閉分頁",
        @"打印…": @"列印…",

        // ── 主菜单 · 编辑 ──
        @"编辑": @"編輯",
        @"撤销": @"復原",
        @"重做": @"重做",
        @"剪切": @"剪下",
        @"粘贴": @"貼上",
        @"全选": @"全選",
        @"快速直达": @"快速直達",
        @"搜索正文": @"搜尋內文",

        // ── 主菜单 · 显示 ──
        @"显示": @"顯示",
        @"后退": @"返回",
        @"前进": @"前進",
        @"显示大纲": @"顯示大綱",
        @"随手记": @"隨手記",
        @"最近": @"最近",
        @"重新载入": @"重新載入",
        @"放大": @"放大",
        @"缩小": @"縮小",
        @"实际大小": @"實際大小",
        @"进入全屏": @"進入全螢幕",

        // ── 主菜单 · 库 ──
        @"库": @"筆記庫",
        @"选择文件夹…": @"選擇資料夾…",
        @"在浏览器里打开门户": @"在瀏覽器中打開門戶",
        @"打开工具目录": @"打開工具資料夾",

        // ── 主菜单 · 窗口 / 帮助 ──
        @"窗口": @"視窗",
        @"最小化": @"縮到最小",
        @"缩放": @"縮放",
        @"前置全部窗口": @"將全部移到最前面",
        @"帮助": @"輔助說明",
        @"AM·Note 帮助": @"AM·Note 輔助說明",
        @"快捷键一览": @"快速鍵一覽",

        // ── 服务信息 ──
        @"端口：%@\n进程：%@\n窗口：%@\n工具目录：%@\n解释器：%@":
            @"連接埠：%@\n程序：%@\n視窗：%@\n工具資料夾：%@\n直譯器：%@",
        @"还没起来": @"尚未啟動",
        @"未启动": @"未啟動",
        @"复用外部实例": @"重用外部實例",
        @"开着": @"開啟中",
        @"关着（服务常驻，双击一份 md 会自己弹回来）":
            @"已關閉（服務持續運行，按兩下一份 md 會自己彈回來）",
        @"未找到": @"找不到",
        @"无": @"無",

        // ── 快捷键一览 ──
        @"快捷键": @"快速鍵",
        @"⌘T 新建标签页（开始页）\n⌘N 新建窗口\n⌘L 搜索\n"
         "⌘[ / ⌘] 后退 / 前进\n⌥⌘C 拷贝路径\n⇧⌘R 在访达中显示\n⌥⌘O 把这份放到独立阅读窗\n"
         "⌘E 进入编辑\n⌘S 存储\n⌘⌫ 移到废纸篓\n⌘W 关闭标签／仅剩起始页时关窗口\n"
         "⇧⌘W 关闭窗口\n⌘P 打印\n"
         "⌃Tab 切换标签\n"
         "⌘K 快速直达\n⌥⌘K 搜索正文\n⌥⌘I 显示大纲\n"
         "⌘F 在本页查找\n⌘R 重新载入\n"
         "⌃⌘F 进入全屏\nEsc 关浮层 / 退出编辑":
            @"⌘T 新增分頁（開始頁）\n⌘N 新增視窗\n⌘L 搜尋\n"
             "⌘[ / ⌘] 返回 / 前進\n⌥⌘C 拷貝路徑\n⇧⌘R 在 Finder 中顯示\n"
             "⌥⌘O 在新視窗中打開\n"
             "⌘E 進入編輯\n⌘S 儲存\n⌘⌫ 移到垃圾桶\n⌘W 關閉分頁／只剩開始頁時關閉視窗\n"
             "⇧⌘W 關閉視窗\n⌘P 列印\n"
             "⌃Tab 切換分頁\n"
             "⌘K 快速直達\n⌥⌘K 搜尋內文\n⌥⌘I 顯示大綱\n"
             "⌘F 在本頁尋找\n⌘R 重新載入\n"
             "⌃⌘F 進入全螢幕\nEsc 關閉浮層 / 退出編輯",

        // ── 未保存改动 ──
        @"有改动还没保存": @"有更改尚未儲存",
        @"关掉这个窗口，正在编辑的改动会丢掉。":
            @"關閉這個視窗，正在編輯的更改會遺失。",
        @"回去保存": @"回去儲存",
        @"直接关闭": @"直接關閉",
        @"关掉窗口会把门户卸下来，正在编辑的稿子会丢掉改动。服务和状态栏图标照常留着。":
            @"關閉視窗會把門戶卸下來，正在編輯的稿件會遺失更改。服務和選單列圖像照常保留。",
        @"安装更新要退出 AM·Note。未保存的改动会丢掉。":
            @"安裝更新要結束 AM·Note。未儲存的更改會遺失。",
        @"放弃改动并更新": @"放棄更改並更新",
        @"独立窗口里正在编辑的稿子有未保存的改动，现在退出会丢掉。":
            @"獨立視窗中正在編輯的稿件有未儲存的更改，現在結束會遺失。",
        @"直接退出": @"直接結束",
        @"门户里正在编辑的稿子有未保存的改动，现在退出会丢掉。":
            @"門戶中正在編輯的稿件有未儲存的更改，現在結束會遺失。",

        // ── 更新 ──
        @"连不上 GitHub。可以一会儿再试，或到仓库 Releases 手动下载。":
            @"連不上 GitHub。可以稍後再試，或到儲存庫的 Releases 頁面手動下載。",
        @"GitHub 上这个版本没有 mac 安装包（AMNote-mac.zip）。":
            @"GitHub 上這個版本沒有 Mac 安裝套件（AMNote-mac.zip）。",
        @"已是最新版本": @"已是最新版本",
        @"当前是 %@。": @"目前是 %@。",
        @"现在检查不了更新": @"現在無法檢查更新",
        @"连不上 GitHub。": @"連不上 GitHub。",
        @"打开下载页": @"打開下載頁面",
        @"现在是 %@。安装会替换当前的 AM·Note，装完自动打开。\n笔记还在原来的文件夹里，不会被动。":
            @"目前是 %@。安裝會取代現有的 AM·Note，裝好後自動打開。\n"
             "筆記仍在原來的資料夾裡，不會被更動。",
        @"有新版本 %@": @"有新版本 %@",
        @"安装更新": @"安裝更新",
        @"稍后": @"稍後",
        @"跳过此版本": @"略過此版本",
        @"更新 AM·Note": @"更新 AM·Note",
        @"正在下载…": @"正在下載…",
        @"下载地址不是 GitHub，已中止。": @"下載網址不是 GitHub，已中止。",
        @"正在下载 %@…": @"正在下載 %@…",
        @"正在下载 %@…  %.1f / %.1f MB": @"正在下載 %@…  %.1f / %.1f MB",
        @"下载失败。": @"下載失敗。",
        @"下载失败（HTTP %ld）。": @"下載失敗（HTTP %ld）。",
        @"建临时目录失败。": @"無法建立暫存資料夾。",
        @"保存安装包失败。": @"無法儲存安裝套件。",
        @"正在校验…": @"正在驗證…",
        @"安装包大小不对（%llu，期望 %@）。": @"安裝套件大小不對（%llu，預期 %@）。",
        @"不认识的校验算法：%@。": @"不認識的檢查碼演算法：%@。",
        @"算不了安装包的校验值。": @"無法計算安裝套件的檢查碼。",
        @"安装包校验失败，没有安装。请到 GitHub Releases 重新下载。":
            @"安裝套件驗證失敗，沒有安裝。請到 GitHub Releases 重新下載。",
        @"解压失败。": @"解壓縮失敗。",
        @"解压安装包失败。": @"解壓縮安裝套件失敗。",
        @"压缩包里没有 AM·Note.app。": @"壓縮檔裡沒有 AM·Note.app。",
        @"安装包的 Bundle ID 不是 app.amnote，已中止。":
            @"安裝套件的 Bundle ID 不是 app.amnote，已中止。",
        @"包里的版本是 %@，不比现在的新。": @"套件裡的版本是 %@，不比現在的新。",
        @"包里的版本是 %@，比 GitHub 上的 %@ 还旧。":
            @"套件裡的版本是 %@，比 GitHub 上的 %@ 還舊。",
        @"安装包里缺少可执行文件。": @"安裝套件裡缺少可執行檔。",
        @"挪新版本失败。": @"無法移動新版本。",
        @"当前不是从 .app 运行的，没法自动替换。请到 GitHub 手动下载。":
            @"目前不是從 .app 執行，無法自動取代。請到 GitHub 手動下載。",
        @"找不到解好的新版本。": @"找不到已解壓的新版本。",
        @"写更新脚本失败。": @"無法寫入更新腳本。",
        @"拉不起更新脚本。": @"無法啟動更新腳本。",
        };
    });
    return d;
}

/// 查词。查不到就回落到键（去掉 `##语境` 后缀）——zh-Hans 走的就是这条。
static inline NSString *L(NSString *key) {
    if (!key.length) return key ?: @"";
    NSString *lang = AMNLanguage();
    NSDictionary<NSString *, NSString *> *d = nil;
    if ([lang isEqualToString:@"en"])         d = AMNDictEN();
    else if ([lang isEqualToString:@"zh-HK"]) d = AMNDictHK();
    NSString *v = d[key];
    if (v.length) return v;
    return AMNStripContext(key);
}

// ───────────────────── 欢迎笔记（建库时按当时的语言写）─────────────────────
//
// 已经存在的欢迎信不回溯：换语言不改动用户库里的文件。

static inline NSString *AMNWelcomeFileName(void) {
    NSString *lang = AMNLanguage();
    if ([lang isEqualToString:@"en"])    return @"Welcome.md";
    if ([lang isEqualToString:@"zh-HK"]) return @"歡迎.md";
    return @"欢迎.md";
}

static inline NSString *AMNWelcomeNote(void) {
    NSString *lang = AMNLanguage();
    if ([lang isEqualToString:@"en"]) {
        return
        @"# Welcome to AM·Note\n"
         "\n"
         "This is the first note AM·Note left for you. Edit it or delete it — either is fine.\n"
         "\n"
         "## Three things are enough\n"
         "\n"
         "- **⌘K finds any note**: it doesn't matter where you filed it — titles and body "
         "text are searched together.\n"
         "- **Double-click the text to edit**: there's no Save button; your changes go back "
         "into the original file.\n"
         "- **⤢ Focus mode**: everything else folds away and only the text is left.\n"
         "\n"
         "## Your files stay where they are\n"
         "\n"
         "The Markdown files and web pages in this folder stay right where they are. "
         "AM·Note only reads and indexes them: nothing is uploaded, reformatted or moved. "
         "Manage them with Finder, Git or iCloud however you like — open one in another "
         "editor and it's still the same file.\n"
         "\n"
         "To write the next one: press ⌘T for a new tab, or just create a new .md in this "
         "folder.\n";
    }
    if ([lang isEqualToString:@"zh-HK"]) {
        return
        @"# 歡迎使用 AM·Note\n"
         "\n"
         "這是 AM·Note 給你放的第一篇筆記。改它、刪它都可以。\n"
         "\n"
         "## 三件事就夠了\n"
         "\n"
         "- **⌘K 找任何一篇筆記**：不記得放在哪裡也沒關係，標題和內文一起搜。\n"
         "- **按兩下內文即可編輯**：不用按儲存，更改會存回原來那個檔案。\n"
         "- **⤢ 專注模式**：其餘都收起來，螢幕上只剩內文。\n"
         "\n"
         "## 檔案都留在原處\n"
         "\n"
         "這個資料夾裡的 Markdown 和網頁還在原處。AM·Note 只是讀它們、建立索引："
         "不上載、不改格式、不搬家。用 Finder、Git、iCloud 怎麼管都可以，"
         "換別的編輯器打開還是同一份檔案。\n"
         "\n"
         "想寫下一篇：⌘T 開一個新分頁，或者直接在這個資料夾裡新增一個 .md。\n";
    }
    return
    @"# 欢迎使用 AM·Note\n"
     "\n"
     "这是 AM·Note 给你放的第一篇笔记。改它、删它都行。\n"
     "\n"
     "## 三件事就够了\n"
     "\n"
     "- **⌘K 找任何一篇笔记**：不记得放在哪儿也没关系，标题和正文一起搜。\n"
     "- **双击正文即可编辑**：不用按保存，改动会存回原来那个文件。\n"
     "- **⤢ 专注模式**：其余都收起来，屏幕上只剩正文。\n"
     "\n"
     "## 文件都留在原地\n"
     "\n"
     "这个文件夹里的 Markdown 和网页还在原处。AM·Note 只是读它们、建索引："
     "不上传、不改格式、不搬家。用访达、Git、iCloud 怎么管都行，"
     "换别的编辑器打开还是同一份文件。\n"
     "\n"
     "想写下一篇：⌘T 开个新标签页，或者直接在这个文件夹里新建一个 .md。\n";
}

#endif  // AMN_APP_SHELL_I18N_H
