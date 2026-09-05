// AM·Note · 原生客户端壳
//
// 一个 WKWebView 窗口，加一个 portal_server.py 子进程。点图标就是「起服务 → 扫库 → 开窗口」。
// 关窗口只关窗口，服务和状态栏图标留着；要连服务一起收掉，走 ⌘Q 或状态栏菜单里的「退出」。
// 没有 Chrome，没有 PWA，没有 LaunchAgent。
//
// 编译与打包走 `python3 build_app.py`，不要手动 clang 完往 .app 里拷。
//
// ─── 为什么是 Objective-C 不是 Swift（20260817 实测）───────────────────────
//
// 本机 Command Line Tools 的 /usr/include/swift/ 下躺着两份内容相同的 modulemap：
// module.modulemap（2023 年那次装的残留）和 bridging.modulemap（现行），两份都定义
// SwiftBridging，于是 swiftc 只要 import AppKit 就报 redefinition of module 直接失败。
// 系统目录不能动（要 sudo，而且下次 CLT 更新又会变），能绕的只有 VFS overlay 把其中一份
// 映射成空文件——实测这样会让整个模块缓存失效，一个三行的 hello world 编了 90 秒还没完。
// 把这种绕法烤进构建脚本是给以后埋雷。
// clang 编 Objective-C 不碰那个目录，同样 import AppKit + WebKit，实测 0.65 秒。
// 所以这里选 ObjC：代码啰嗦一点，但构建是干净的一条 clang 命令，换台机器也能跑。
//
// ─── 九条设计约束，改代码前先看 ─────────────────────────────────────────
//
// 1. ⌘W / ⌘S / ⌘K / ⌘E / ⌘T / ⌘N / ⌘L 现在进菜单了（20260822 收 ⌘W ⌘S，
//    20260829 加 ⌘K ⌘E；浏览器壳这一轮把 ⌘T ⌘N ⌘L 也收进来）。
//    旧规矩是「这几个键一律不许出现在原生菜单里」：NSMenu 的 performKeyEquivalent 早于
//    WKWebView 收到事件，菜单一旦占了这个键，门户自己 addEventListener 绑的同名快捷键
//    就永久失灵，表现是按了没反应。
//    新做法把这条正面解决掉：菜单项的 target 指向 AppDelegate，action 方法里
//    evaluateJavaScript 调门户挂在 window.AMN 上的同名函数
//    （closeTab / save / quickOpen / enterEdit / newTab / newWindow / focusOmnibox）。
//    菜单抢到键之后调的是同一个函数，结果一致；快捷键在菜单里查得到，辅助功能也读得到。
//    代价是门户必须导出 window.AMN 那一套接口。门户没导出时，AMN.state() 拿不到值，
//    这些菜单项一律保持灰态——按不动，但不会出错。
//    ⌘W：主窗和完整浏览器辅窗关当前标签；仅剩起始页时关窗口。独立阅读窗没有标签，
//    直接 performClose。⇧⌘W 仍是显式「关闭窗口」。
//    **⌘K 是「快速直达」浮层（AMN.quickOpen）**，⌥⌘K 仍是 AMN.focusSearch；
//    ⌘L 是地址栏（AMN.focusOmnibox）。⌘T 新建标签（AMN.newTab），⌘N 新建完整窗口
//    （AMN.newWindow），不再占用「新建随手记」。这些名字跟前端同名，改一边就得改另一边。
// 2. Edit 菜单必须有。WKWebView 里的 ⌘C/⌘V/⌘A/⌘Z 是靠菜单项的 selector 生效的，
//    菜单里没有这些项，门户的 MD 编辑器就没法复制粘贴。
// 3. JS 的 alert / confirm / prompt 必须由 WKUIDelegate 接住，window.open 和
//    target="_blank" 必须由 createWebViewWithConfiguration 接住。不接的话：
//      · confirm() 恒返回 NO → 门户里「改了没保存，确定吗」全部恒为取消，脏标签关不掉、
//        编辑退不出、冲突保存走不通、阅读器离不开。
//      · window.open 返回 null → 「在浏览器里打开」按钮点了没反应，MD 正文里所有
//        target="_blank" 的链接全成死链。
//    确认框现在是**结构化的同步 confirm()**：门户 7 处确认调用点都在同步流程中间
//    （关标签、放弃编辑、覆盖保存、恢复留档），改成异步消息要重写这几条链路，不值当。
//    所以标题、正文、按钮、破坏性标记都塞在 confirm() 那一个字符串里，壳在
//    runJavaScriptConfirmPanelWithMessage: 里拆开还原成正经 NSAlert。格式见那个方法。
//    amn 通道上的异步 {type:'confirm'} 也实现了，当前门户不发，留给以后。
// 4. 服务常驻、窗口按需（20260823 改）。关窗口时 **必须把 WKWebView 整块拆掉**，
//    不能只是把窗口 order out 留着网页在后台跑：WebKit 对不可见窗口里的页面
//    只降频、不停表，一个看不见的门户还在每 3 秒轮询，白烧电。
//    代价：关窗口＝卸门户，未保存的改动会丢，所以 windowShouldClose: 要跟 ⌘Q 一样拦一道。
//    （Agent 的「打开这份」队列 2026-08-25 撤了——交付只给路径，不推窗口。
//    双击 md 和 amnote:// 进来的那条还在，走 queueOpen:，它自己会把窗口开回来。）
// 5. 原生 NSToolbar 不再挂上窗口。标签、地址栏、书签栏都画在网页顶上那 52px 里，
//    再挂一排原生按钮会跟网页顶栏抢位置，交通灯也会被顶下去。buildToolbar 还留着
//    （自定义搜索框那条线没拆），只是 buildWindow 不再把它赋给 _win.toolbar。
// 6. 起服务前先验 Python 能不能跑（20260829 加）。只判「文件存在且可执行」不够：
//    没装 Xcode 命令行工具的机器上 /usr/bin/python3 是个占位壳子——存在、可执行、
//    一跑就弹系统那句「要装命令行工具吗」然后非零退出。表现是 AM·Note 点开一直转，
//    最后报「服务 25 秒内没有报出端口号」，跟真正的原因差着十万八千里。
//    所以 fork 之前先拿 `-c pass` 把三个候选挨个跑一遍（各 3 秒超时），
//    一个都不通就直接告诉用户去 xcode-select --install。
// 7. 视觉无边框（20260829 加）。窗口仍保留系统圆角、阴影、拖动、
//    缩放和交通灯；只收掉视觉噪声：内容铺到标题栏背后、标题栏不画分界线、
//    标题字符串藏起来以免压在标签上。网页在 shell 模式给交通灯预留 52px，
//    这两个数是一对；改标题栏高度时要同步看 `body.shell #app/#hub`。**网页主题和原生
//    标题栏也必须同步**：门户发 `{type:'theme', value:'auto|light|dark'}`，壳给窗口设置
//    Aqua / DarkAqua / 跟随系统。漏掉这条会让白色标题和白色按钮落在浅色网页上，近乎消失。
// 8. 冷启动时 application:openURLs: 会在 applicationDidFinishLaunching: 之前到达。
//    _pendingOpens 必须在 init 里建好；didFinishLaunching 里不许换成新数组，
//    否则访达双击进来的路径会被静默丢掉，窗口只停在首页。flushPending
//    在网页还没 _loadedOnce 且 _pageReady 时也不许倒掉队列——amn: 这时
//    会因为壳还没就绪而把调用丢掉。
// 9. 更新只问 GitHub Releases，不另搭服务器、不上 Sparkle（没买开发者签名，
//    也撑不起再塞一个 framework）。默认第二次启动起，大约一天查一次；
//    只访问 github.com / api.github.com，不上传笔记。装新版本的顺序是：
//    下载 AMNote-mac.zip → 有 digest 就核 sha256 → 解开后核对 bundle id
//    必须是 app.amnote → 退出后脚本 ditto 覆盖当前 .app → open。
//    已经在用的旧版不会凭空获得这条能力，得先手动装一版带检查的。

#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#import <CommonCrypto/CommonDigest.h>
#import <signal.h>
#import <unistd.h>

// ─────────────────────────── 配置 ───────────────────────────

/// 用户选的库根，存在 defaults 里，下次直接用。
static NSString *const kVaultKey = @"AMNVaultPath";

/// 起服务等多久算失败。冷启动扫库很快，25 秒足够宽。
static const NSTimeInterval kStartTimeout = 25.0;

/// 网页迟迟不 didFinish 时的兜底：到点了不管加载完没有都把窗口放出来，
/// 免得「等就绪再显示」变成「一个看不见的 app 挂在 Dock 上」。
static const NSTimeInterval kShowTimeout = 12.0;

static const CGFloat kWinW = 1320, kWinH = 880;

/// 列表不再是默认布局，最小宽度从 900 收到 720，小屏也能开出一栏正文。
static const CGFloat kMinW = 720, kMinH = 620;

/// 独立文稿窗口（`/portal?solo=1&doc=…`）。它只有正文一栏，没有边栏也没有列表，
/// 所以 kMinW 那个下限不适用——按一栏正文的最小可读宽度卡。
static const CGFloat kSoloW = 1020, kSoloH = 940;
static const CGFloat kSoloMinW = 480, kSoloMinH = 400;

static const CGFloat kFindBarH = 38;
static const CGFloat kChromeH = 52;

/// 菜单校验不许同步等 JS。做法是定时把 AMN.state() 的结果抓回来缓存，
/// validateMenuItem: 只读缓存。0.5 秒是「按下菜单前状态已经对了」的够用值。
static const NSTimeInterval kStateTick = 0.5;

/// 也跟 /__open 回给 Agent 的那句「门户开着的话 3 秒内打开」对得上。

/// Python 预检的单次超时。3 秒足够跑起一个解释器再退出；卡在这个量级的
/// 多半就是 CLT 那个占位壳子在等用户点弹窗，等下去也不会有结果。
static const NSTimeInterval kPyProbe = 3.0;

/// GitHub Releases。仓库、安装包文件名、检查间隔都写死——这个壳只服务这一个 app。
static NSString *const kUpdateRepo     = @"Qiululu667/amnote";
static NSString *const kUpdateAsset    = @"AMNote-mac.zip";
static NSString *const kAutoCheckKey   = @"AMNAutoCheckUpdates";
static NSString *const kLastCheckKey   = @"AMNLastUpdateCheck";
static NSString *const kSkipVerKey     = @"AMNSkippedUpdateVersion";
static NSString *const kLaunchCountKey = @"AMNLaunchCount";
static const NSTimeInterval kUpdateEvery = 24.0 * 60.0 * 60.0;
static const NSTimeInterval kUpdateDelay = 18.0;

// 工具栏项标识（设计约束 5：默认就三件，标识随之从 amn.sidebar 改成 amn.lists）
static NSString *const kTBLists  = @"amn.lists";
static NSString *const kTBSearch = @"amn.search";
static NSString *const kTBNew    = @"amn.new";

/// 解释器按顺序试。第一个是 Apple 自带的：scan_tags / apply_tags / prep_batches /
/// portal_server 全是标准库，3.9.6 实测 import 得动，所以优先用它，免得依赖用户装没装
/// python.org 或 homebrew 的 python。
static NSArray<NSString *> *pythonCandidates(void) {
    return @[ @"/usr/bin/python3", @"/usr/local/bin/python3", @"/opt/homebrew/bin/python3" ];
}

static NSString *pickPython(void) {
    NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *p in pythonCandidates()) {
        if ([fm isExecutableFileAtPath:p]) return p;
    }
    return nil;
}

/// 设计约束 6：真跑一次 `-c pass`，退出码 0 才算数。
/// 输入输出全接到 /dev/null——不接的话子进程往管道里写满就卡住，那是另一种超时。
/// 超时了先 SIGTERM 再 SIGKILL，不留孤儿。
static BOOL pythonRuns(NSString *py) {
    NSTask *t = [NSTask new];
    t.executableURL = [NSURL fileURLWithPath:py];
    t.arguments = @[ @"-c", @"pass" ];
    t.standardInput  = NSFileHandle.fileHandleWithNullDevice;
    t.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    t.standardError  = NSFileHandle.fileHandleWithNullDevice;
    if (![t launchAndReturnError:NULL]) return NO;
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:kPyProbe];
    while (t.isRunning && [deadline timeIntervalSinceNow] > 0) usleep(20000);
    if (t.isRunning) {
        [t terminate];
        usleep(100000);
        if (t.isRunning) kill(t.processIdentifier, SIGKILL);
        return NO;                                   // 还活着＝超时，terminationStatus 这时不能读
    }
    return t.terminationStatus == 0;
}

/// 挑一个「真的能跑」的解释器。存在但跑不动的（CLT 占位壳子）跳过接着往下试。
static NSString *pickRunnablePython(void) {
    NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *p in pythonCandidates()) {
        if (![fm isExecutableFileAtPath:p]) continue;
        if (pythonRuns(p)) return p;
    }
    return nil;
}

/// 路径对齐：POSIX 标准化 + NFC。defaults / 访达 / 状态接口来源可能不一致。
static NSString *normPath(NSString *p) {
    if (!p.length) return p;
    return p.stringByStandardizingPath.precomposedStringWithCanonicalMapping;
}

/// ~/Library/Application Support/AMNote/。端口文件和口令文件都放这里。
static NSString *supportDir(void) {
    NSString *dir = [NSHomeDirectory() stringByAppendingPathComponent:
                     @"Library/Application Support/AMNote"];
    [[NSFileManager defaultManager] createDirectoryAtPath:dir
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:nil];
    return dir;
}

static NSString *portFilePath(void) {
    return [supportDir() stringByAppendingPathComponent:@"portal.port"];
}

static NSString *tokenFilePath(void) {
    return [supportDir() stringByAppendingPathComponent:@"portal.token"];
}

/// 工具目录：优先 bundle Resources 里的 portal_server.py；开发时再找源码 src。
static NSString *locateTools(void) {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSMutableArray<NSString *> *cands = [NSMutableArray array];
    NSString *res = NSBundle.mainBundle.resourcePath;
    if (res.length) [cands addObject:res];

    NSString *exeDir = NSBundle.mainBundle.executablePath.stringByDeletingLastPathComponent;
    if (exeDir.length) {
        [cands addObject:[exeDir stringByAppendingPathComponent:@"src"]];
        [cands addObject:exeDir];
    }
    NSString *cwd = fm.currentDirectoryPath;
    if (cwd.length) {
        [cands addObject:[cwd stringByAppendingPathComponent:@"src"]];
        [cands addObject:cwd];
    }
    for (NSString *c in cands) {
        if ([fm fileExistsAtPath:[c stringByAppendingPathComponent:@"portal_server.py"]]) return c;
    }
    return nil;
}

/// 库根：只读 NSUserDefaults，不往上找任何标志文件。目录没了就当没选过。
static NSString *locateRoot(void) {
    NSString *p = [NSUserDefaults.standardUserDefaults stringForKey:kVaultKey];
    if (!p.length) return nil;
    BOOL dir = NO;
    if (![NSFileManager.defaultManager fileExistsAtPath:p isDirectory:&dir] || !dir) return nil;
    return p;
}

static void saveVault(NSString *path) {
    if (!path.length) return;
    [NSUserDefaults.standardUserDefaults setObject:path forKey:kVaultKey];
    [NSUserDefaults.standardUserDefaults synchronize];
}

/// 只建 .amnote。随手记目录留给服务端第一次新建时再创建。
static void prepareVault(NSString *path) {
    if (!path.length) return;
    NSString *dot = [path stringByAppendingPathComponent:@".amnote"];
    [[NSFileManager defaultManager] createDirectoryAtPath:dot
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:nil];
}

// ── 更新用的纯函数。不碰 UI，方便核对版本和包是不是自己的。

static BOOL amnAutoCheckOn(void) {
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    if (![d objectForKey:kAutoCheckKey]) return YES;   // 没写过＝默认开
    return [d boolForKey:kAutoCheckKey];
}

static NSString *amnShortVersion(void) {
    NSString *v = NSBundle.mainBundle.infoDictionary[@"CFBundleShortVersionString"];
    return v.length ? v : @"0";
}

/// 去掉 v 前缀和 -beta 这类尾巴，只留 1.2.3。
static NSString *amnStripVer(NSString *s) {
    if (!s.length) return @"0";
    s = [s stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if ([s hasPrefix:@"v"] || [s hasPrefix:@"V"]) s = [s substringFromIndex:1];
    NSRange cut = [s rangeOfCharacterFromSet:[NSCharacterSet characterSetWithCharactersInString:@"- +"]];
    if (cut.location != NSNotFound) s = [s substringToIndex:cut.location];
    return s.length ? s : @"0";
}

static NSInteger amnCmpVersion(NSString *a, NSString *b) {
    NSArray *pa = [amnStripVer(a) componentsSeparatedByString:@"."];
    NSArray *pb = [amnStripVer(b) componentsSeparatedByString:@"."];
    NSUInteger n = MAX(pa.count, pb.count);
    for (NSUInteger i = 0; i < n; i++) {
        NSInteger ia = i < pa.count ? [pa[i] integerValue] : 0;
        NSInteger ib = i < pb.count ? [pb[i] integerValue] : 0;
        if (ia < ib) return -1;
        if (ia > ib) return 1;
    }
    return 0;
}

/// 下载地址只认 GitHub。被跳到别的域就中止，避免 zip 被劫持。
static BOOL amnURLTrusted(NSURL *u) {
    if (!u) return NO;
    if (![u.scheme.lowercaseString isEqualToString:@"https"]) return NO;
    NSString *h = u.host.lowercaseString;
    if (!h.length) return NO;
    if ([h isEqualToString:@"github.com"] || [h hasSuffix:@".github.com"]) return YES;
    if ([h isEqualToString:@"githubusercontent.com"] || [h hasSuffix:@".githubusercontent.com"]) return YES;
    return NO;
}

static NSString *amnPlainNotes(NSString *md) {
    if (!md.length) return @"";
    NSString *s = [md stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    while ([s containsString:@"\n\n\n"])
        s = [s stringByReplacingOccurrencesOfString:@"\n\n\n" withString:@"\n\n"];
    if (s.length > 1000) s = [[s substringToIndex:1000] stringByAppendingString:@"…"];
    return s;
}

static NSString *amnSHA256File(NSString *path) {
    NSInputStream *in = [NSInputStream inputStreamWithFileAtPath:path];
    if (!in) return nil;
    [in open];
    if (in.streamStatus == NSStreamStatusError) { [in close]; return nil; }
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    uint8_t buf[65536];
    for (;;) {
        NSInteger n = [in read:buf maxLength:sizeof(buf)];
        if (n == 0) break;
        if (n < 0) { [in close]; return nil; }
        CC_SHA256_Update(&ctx, buf, (CC_LONG)n);
    }
    [in close];
    unsigned char dig[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(dig, &ctx);
    NSMutableString *hex = [NSMutableString stringWithCapacity:CC_SHA256_DIGEST_LENGTH * 2];
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) [hex appendFormat:@"%02x", dig[i]];
    return hex;
}

static NSString *amnFindApp(NSString *dir) {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSArray *items = [fm contentsOfDirectoryAtPath:dir error:nil];
    NSMutableArray<NSString *> *apps = [NSMutableArray array];
    for (NSString *n in items) {
        if ([n hasPrefix:@"."]) continue;
        NSString *p = [dir stringByAppendingPathComponent:n];
        if ([n.pathExtension.lowercaseString isEqualToString:@"app"]) [apps addObject:p];
        BOOL isDir = NO;
        if (![n.pathExtension.lowercaseString isEqualToString:@"app"] &&
            [fm fileExistsAtPath:p isDirectory:&isDir] && isDir) {
            for (NSString *n2 in [fm contentsOfDirectoryAtPath:p error:nil]) {
                if ([n2.pathExtension.lowercaseString isEqualToString:@"app"])
                    [apps addObject:[p stringByAppendingPathComponent:n2]];
            }
        }
    }
    for (NSString *p in apps)
        if ([p.lastPathComponent isEqualToString:@"AM·Note.app"]) return p;
    return apps.count == 1 ? apps.firstObject : nil;
}

static NSString *amnUserAgent(void) {
    return [NSString stringWithFormat:@"AMNote/%@ (https://github.com/%@)",
            amnShortVersion(), kUpdateRepo];
}

/// 问某个端口上的 /__status。成功返回解析后的 JSON。8770 直接当没有，不去连。
static NSDictionary *fetchStatus(NSInteger port, NSTimeInterval timeout) {
    if (port == 8770) return nil;
    NSURL *u = [NSURL URLWithString:
        [NSString stringWithFormat:@"http://127.0.0.1:%ld/__status", (long)port]];
    if (!u) return nil;
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:u];
    req.timeoutInterval = timeout;
    __block NSDictionary *out = nil;
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    [[NSURLSession.sharedSession dataTaskWithRequest:req
        completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {
            NSHTTPURLResponse *h = (NSHTTPURLResponse *)r;
            if (h.statusCode == 200 && d.length) {
                id obj = [NSJSONSerialization JSONObjectWithData:d options:0 error:nil];
                if ([obj isKindOfClass:NSDictionary.class]) out = obj;
            }
            dispatch_semaphore_signal(sem);
        }] resume];
    dispatch_semaphore_wait(sem,
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)((timeout + 0.5) * NSEC_PER_SEC)));
    return out;
}

/// SF Symbol。名字在这台机器上不存在时返回 nil，调用方要自己兜住（工具栏项会退成纯文字）。
static NSImage *sym(NSString *name, NSString *desc) {
    if (@available(macOS 11.0, *)) {
        return [NSImage imageWithSystemSymbolName:name accessibilityDescription:desc];
    }
    return nil;
}

/// 菜单栏那一颗：应用图标里的白云，抠成 18pt template，颜色跟菜单栏走。
/// 图由 make_icon.py 从母版抽出，打进 Resources/menubar-cloud(@2x).png。
/// 缺文件时退到 SF cloud.fill，再没有就让调用方改成文字。
static NSImage *menubarCloudIcon(void) {
    NSString *res = [NSBundle mainBundle].resourcePath;
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *p2 = [res stringByAppendingPathComponent:@"menubar-cloud@2x.png"];
    NSString *p1 = [res stringByAppendingPathComponent:@"menubar-cloud.png"];
    NSImage *img = nil;
    if ([fm fileExistsAtPath:p2])
        img = [[NSImage alloc] initWithContentsOfFile:p2];
    else if ([fm fileExistsAtPath:p1])
        img = [[NSImage alloc] initWithContentsOfFile:p1];
    if (img) {
        img.size = NSMakeSize(18.0, 18.0);
        img.template = YES;
        return img;
    }
    NSImage *sf = sym(@"cloud.fill", @"AM·Note");
    if (sf) sf.template = YES;
    return sf;
}

/// 门户最近一次告诉壳的主题。全屏会重建标题栏，必须拿这个值再刷一遍底色。
static NSString *gAppliedTheme = @"auto";

static BOOL themeIsDark(NSString *theme, NSWindow *window) {
    NSString *match = [window.effectiveAppearance bestMatchFromAppearancesWithNames:
                       @[ NSAppearanceNameAqua, NSAppearanceNameDarkAqua ]];
    return [theme isEqualToString:@"dark"] ||
           (![theme isEqualToString:@"light"] && [match isEqualToString:NSAppearanceNameDarkAqua]);
}

/// 网页正文 / WebView 垫色，对应 template.html 的 --win。
static NSColor *surfaceColorForTheme(NSString *theme, NSWindow *window) {
    return themeIsDark(theme, window)
        ? [NSColor colorWithSRGBRed:37.0/255.0 green:41.0/255.0 blue:49.0/255.0 alpha:1.0]
        : [NSColor colorWithSRGBRed:1.0 green:1.0 blue:1.0 alpha:1.0];
}

/// 标题栏 / 交通灯背后那一层，对应 template.html 的 --chrome。
/// 标签条是灰的，这里必须跟着灰，不然红绿灯周围会露出一块白。
static NSColor *chromeColorForTheme(NSString *theme, NSWindow *window) {
    return themeIsDark(theme, window)
        ? [NSColor colorWithSRGBRed:34.0/255.0 green:38.0/255.0 blue:45.0/255.0 alpha:1.0]
        : [NSColor colorWithSRGBRed:247.0/255.0 green:248.0/255.0 blue:250.0/255.0 alpha:1.0];
}

/// 把标题栏里那层 vibrancy 收掉，露出底下的网页。
/// 全屏时系统会重新塞进 NSVisualEffectView，不处理就会重新变成一条白带。
///
/// 这几层视图都排在 contentView **之上**（themeFrame 的兄弟节点里靠后），
/// 给它们刷不透明底色等于拿 chrome 色把网页顶部 32pt 糊死：标签名、×、＋、
/// 右侧 app 图标全被盖住，只露出标签条底边那几点。所以一律刷成透明，
/// 让网页自己画的那条标签带透上来。没被网页盖到的地方由
/// `window.backgroundColor`（applyThemeToWindow 里设成 chrome 色）兜底，
/// 颜色一致所以看不出接缝。
static void paintTitlebarSurface(NSWindow *window) {
    if (!window) return;
    CGColorRef clear = NSColor.clearColor.CGColor;
    NSButton *closeBtn = [window standardWindowButton:NSWindowCloseButton];
    NSView *titlebar = closeBtn.superview;
    if (titlebar) {
        titlebar.wantsLayer = YES;
        titlebar.layer.backgroundColor = clear;
        for (NSView *child in titlebar.subviews) {
            if ([child isKindOfClass:[NSVisualEffectView class]]) {
                child.hidden = YES;
            }
        }
        NSView *container = titlebar.superview;
        if (container) {
            container.wantsLayer = YES;
            container.layer.backgroundColor = clear;
            for (NSView *child in container.subviews) {
                if ([child isKindOfClass:[NSVisualEffectView class]]) {
                    child.hidden = YES;
                }
            }
        }
    }
    NSView *themeFrame = window.contentView.superview;
    if (!themeFrame) return;
    for (NSView *v in themeFrame.subviews) {
        NSString *cls = NSStringFromClass(v.class);
        if ([cls containsString:@"Titlebar"] || [cls containsString:@"Toolbar"]) {
            v.wantsLayer = YES;
            v.layer.backgroundColor = clear;
        }
    }
}

static void paintWebSurfaces(NSWindow *window, NSColor *surface) {
    if (!window.contentView) return;
    NSMutableArray *stack = [NSMutableArray arrayWithObject:window.contentView];
    while (stack.count) {
        NSView *v = stack.lastObject;
        [stack removeLastObject];
        if ([v isKindOfClass:[WKWebView class]]) {
            WKWebView *w = (WKWebView *)v;
            if (@available(macOS 12.0, *)) {
                w.underPageBackgroundColor = surface;
            }
            w.wantsLayer = YES;
            w.layer.backgroundColor = surface.CGColor;
        }
        if (v.subviews.count) [stack addObjectsFromArray:v.subviews];
    }
}

/// 工具栏两颗图标：圆角矩形＋竖线（列表），圆端十字（新建）。
/// SF Symbol 的 sidebar.left / plus 在 Regular 18pt 下偏方、偏硬，
/// 跟首页搜索胶囊的圆角语言对不上。这里按 Phosphor Light 的线型和圆角自绘，
/// 作为 template image，颜色仍跟标题栏走。
/// 画布 16pt。工具栏若处在 Regular 尺寸模式，系统会把图像放大到槽位，
/// 只改画布看不出变化；真正收小要靠 Small 模式 + 标识换代 + 图像 size 锁死。
static NSImage *roundToolbarIcon(NSString *kind) {
    const CGFloat s = 16.0;
    NSImage *img = [NSImage imageWithSize:NSMakeSize(s, s)
                                  flipped:NO
                           drawingHandler:^BOOL(NSRect dst) {
        [[NSColor blackColor] setStroke];
        if ([kind isEqualToString:@"plus"]) {
            NSBezierPath *p = [NSBezierPath bezierPath];
            p.lineWidth = 1.2;
            p.lineCapStyle = NSLineCapStyleRound;
            p.lineJoinStyle = NSLineJoinStyleRound;
            CGFloat m = 3.35, c = s / 2.0;
            [p moveToPoint:NSMakePoint(m, c)];
            [p lineToPoint:NSMakePoint(s - m, c)];
            [p moveToPoint:NSMakePoint(c, m)];
            [p lineToPoint:NSMakePoint(c, s - m)];
            [p stroke];
        } else {
            NSRect box = NSInsetRect(NSMakeRect(0, 0, s, s), 1.85, 2.15);
            NSBezierPath *rect = [NSBezierPath bezierPathWithRoundedRect:box
                                                                xRadius:2.5
                                                                yRadius:2.5];
            rect.lineWidth = 1.15;
            rect.lineJoinStyle = NSLineJoinStyleRound;
            [rect stroke];
            NSBezierPath *div = [NSBezierPath bezierPath];
            div.lineWidth = 1.15;
            div.lineCapStyle = NSLineCapStyleRound;
            CGFloat x = NSMinX(box) + NSWidth(box) * 0.34;
            [div moveToPoint:NSMakePoint(x, NSMinY(box))];
            [div lineToPoint:NSMakePoint(x, NSMaxY(box))];
            [div stroke];
        }
        return YES;
    }];
    img.template = YES;
    return img;
}

/// 全屏内容视图把网页铺到了标题栏背后，网页和 AppKit 必须共用明暗外观。
/// auto 用 nil 交还系统；显式浅／深则锁定对应外观，让标题和按钮自动取正确颜色。
static void applyThemeToWindow(NSWindow *window, NSString *theme) {
    if (!window) return;
    gAppliedTheme = [(theme.length ? theme : @"auto") copy];
    if ([gAppliedTheme isEqualToString:@"light"]) {
        window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
    } else if ([gAppliedTheme isEqualToString:@"dark"]) {
        window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    } else {
        window.appearance = nil;
    }

    NSColor *surface = surfaceColorForTheme(gAppliedTheme, window);
    NSColor *chrome = chromeColorForTheme(gAppliedTheme, window);
    window.backgroundColor = chrome;
    window.contentView.wantsLayer = YES;
    window.contentView.layer.backgroundColor = surface.CGColor;
    paintWebSurfaces(window, surface);
    paintTitlebarSurface(window);
}

/// AppKit 进入或退出全屏时会重新组织标题栏视图，原来设过的分隔线状态可能被系统重置。
/// 每次窗口形态变化后都补一遍，保证网页和标题栏始终是一整块连续表面。
static void applySeamlessChrome(NSWindow *window) {
    if (!window) return;
    window.titlebarAppearsTransparent = YES;
    window.titlebarSeparatorStyle = NSTitlebarSeparatorStyleNone;
    if (window.toolbar) window.toolbar.showsBaselineSeparator = NO;
    NSWindowStyleMask mask = window.styleMask;
    if ((mask & NSWindowStyleMaskTitled) && !(mask & NSWindowStyleMaskFullSizeContentView)) {
        window.styleMask = mask | NSWindowStyleMaskFullSizeContentView;
    }
    // 完整浏览器窗的标签画在标题栏那一行。标题字符串一旦画出来
    // 会压在标签中间；独立阅读窗没有那条标签带，identifier 把它豁免掉。
    if (![window.identifier isEqualToString:@"amn.solo"])
        window.titleVisibility = NSWindowTitleHidden;
    applyThemeToWindow(window, gAppliedTheme);
}

// ─────────────────────────── 服务 ───────────────────────────

/// portal_server.py 的生命周期。要么复用已经在跑的，要么自己起一个。
/// 复用来的不归我们管，退出时不杀；自己起的一定要杀干净，不留孤儿进程。
@interface PortalService : NSObject
@property (nonatomic, copy)   NSString *toolsDir;
@property (nonatomic, copy)   NSString *vaultPath;
@property (nonatomic, strong) NSTask   *task;
@property (nonatomic, assign) NSInteger port;
@property (nonatomic, assign) BOOL      adopted;
@property (nonatomic, copy)   NSString *stderrText;
- (instancetype)initWithTools:(NSString *)dir vault:(NSString *)vault;
- (BOOL)startAndReturnError:(NSString **)errOut;
- (NSString *)tailStderr;
- (void)stop;
@end

@implementation PortalService {
    NSLock *_lock;
}

- (instancetype)initWithTools:(NSString *)dir vault:(NSString *)vault {
    if ((self = [super init])) {
        _toolsDir = [dir copy];
        _vaultPath = [vault copy];
        _stderrText = @"";
        _lock = [NSLock new];
    }
    return self;
}

- (BOOL)startAndReturnError:(NSString **)errOut {
    NSInteger adopt = [self adoptExisting];
    if (adopt > 0) {
        self.port = adopt;
        self.adopted = YES;
        return YES;
    }
    return [self spawnAndReturnError:errOut];
}

/// 端口文件里记着上一次的端口。还活着、且 /__status 的「库根」就是当前 vault，才复用。
/// 对不上就当没有，自己起。8770 一律不碰。
- (NSInteger)adoptExisting {
    if (!self.vaultPath.length) return 0;
    NSString *raw = [NSString stringWithContentsOfFile:portFilePath()
                                              encoding:NSUTF8StringEncoding error:nil];
    if (!raw.length) return 0;
    NSString *head = [raw componentsSeparatedByString:@"\n"].firstObject;
    NSInteger p = [[head stringByTrimmingCharactersInSet:
                    NSCharacterSet.whitespaceCharacterSet] integerValue];
    if (p < 1024 || p > 65535 || p == 8770) return 0;
    NSDictionary *st = fetchStatus(p, 0.8);
    if (!st) return 0;
    NSString *got = [st[@"库根"] isKindOfClass:NSString.class] ? st[@"库根"] : nil;
    if (!got.length) return 0;
    if (![normPath(got) isEqualToString:normPath(self.vaultPath)]) return 0;
    return p;
}

- (BOOL)spawnAndReturnError:(NSString **)errOut {
    // 设计约束 6：fork 之前先验一遍解释器真能跑。这一句挪不到别处——
    // 放到 fork 之后就变成「25 秒没报出端口号」，用户看不出是缺 Python。
    NSString *py = pickRunnablePython();
    if (!py) {
        if (errOut) *errOut = [NSString stringWithFormat:
            @"这台电脑缺 Python 运行环境\n\n"
             "Python 随 Xcode 命令行工具提供。在终端运行\n\n"
             "    xcode-select --install\n\n"
             "装好后重新打开 AM·Note。\n\n试过这几个位置：\n%@",
            [pythonCandidates() componentsJoinedByString:@"\n"]];
        return NO;
    }

    if (!self.vaultPath.length) {
        if (errOut) *errOut = @"还没有选择文件夹";
        return NO;
    }

    NSTask *t = [NSTask new];
    t.executableURL = [NSURL fileURLWithPath:py];
    t.arguments = @[ @"portal_server.py", @"--root", self.vaultPath ];
    t.currentDirectoryURL = [NSURL fileURLWithPath:self.toolsDir];

    NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy];
    env[@"PYTHONUNBUFFERED"] = @"1";   // 不然第一行端口号会卡在缓冲区里
    env[@"PYTHONIOENCODING"] = @"utf-8";
    env[@"AMNOTE_VAULT"] = self.vaultPath;
    env[@"AMN_TOKEN_FILE"] = tokenFilePath();
    env[@"AMNOTE_PORT_FILE"] = portFilePath();
    t.environment = env;

    NSPipe *out = [NSPipe pipe], *err = [NSPipe pipe];
    t.standardOutput = out;
    t.standardError = err;

    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    __block NSInteger found = 0;
    __block NSMutableString *outBuf = [NSMutableString string];
    __weak typeof(self) weakSelf = self;

    // 第一行 stdout 就是端口号（portal_server.py 末尾 print(port, flush=True)）
    out.fileHandleForReading.readabilityHandler = ^(NSFileHandle *h) {
        NSData *d = h.availableData;
        if (!d.length) return;
        NSString *s = [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding];
        if (!s) return;
        @synchronized (outBuf) {
            [outBuf appendString:s];
            if (found == 0 && [outBuf containsString:@"\n"]) {
                NSString *line = [outBuf componentsSeparatedByString:@"\n"].firstObject;
                NSInteger n = [[line stringByTrimmingCharactersInSet:
                                NSCharacterSet.whitespaceCharacterSet] integerValue];
                if (n > 0) { found = n; dispatch_semaphore_signal(sem); }
            }
        }
    };

    // stderr 全程收着。起不来的时候要靠它告诉用户到底怎么了，
    // 尤其是 TCC 没给权限那种 Operation not permitted。
    err.fileHandleForReading.readabilityHandler = ^(NSFileHandle *h) {
        NSData *d = h.availableData;
        if (!d.length) return;
        NSString *s = [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding];
        if (!s) return;
        typeof(self) me = weakSelf;
        if (!me) return;
        [me->_lock lock];
        me.stderrText = [me.stderrText stringByAppendingString:s];
        [me->_lock unlock];
    };

    // 进程提前死掉也要把等待放开，别干等满 25 秒
    t.terminationHandler = ^(NSTask *tt) {
        if (found == 0) dispatch_semaphore_signal(sem);
    };

    NSError *le = nil;
    if (![t launchAndReturnError:&le]) {
        if (errOut) *errOut = [NSString stringWithFormat:@"python3 启动失败\n\n%@\n%@",
                               py, le.localizedDescription];
        return NO;
    }
    self.task = t;

    long rc = dispatch_semaphore_wait(sem,
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(kStartTimeout * NSEC_PER_SEC)));
    if (rc != 0) {
        [self stop];
        if (errOut) *errOut = [NSString stringWithFormat:
            @"服务 %.0f 秒内没有报出端口号\n\n%@", kStartTimeout, [self tailStderr]];
        return NO;
    }
    if (found <= 0) {
        int code = t.isRunning ? -1 : t.terminationStatus;
        [self stop];
        if (errOut) *errOut = [NSString stringWithFormat:
            @"服务启动即退出（退出码 %d）\n\n%@", code, [self tailStderr]];
        return NO;
    }

    self.port = found;
    [[NSString stringWithFormat:@"%ld", (long)found]
        writeToFile:portFilePath() atomically:YES encoding:NSUTF8StringEncoding error:nil];
    return YES;
}

- (NSString *)tailStderr {
    [_lock lock];
    NSString *t = [self.stderrText stringByTrimmingCharactersInSet:
                   NSCharacterSet.whitespaceAndNewlineCharacterSet];
    [_lock unlock];
    if (!t.length) return @"（服务没有输出任何错误信息）";
    NSArray *lines = [t componentsSeparatedByString:@"\n"];
    if (lines.count > 12) lines = [lines subarrayWithRange:NSMakeRange(lines.count - 12, 12)];
    return [lines componentsJoinedByString:@"\n"];
}

/// 只杀自己起的那个。复用来的不动。
- (void)stop {
    if (self.adopted) return;
    NSTask *t = self.task;
    if (!t || !t.isRunning) return;
    [t terminate];                                   // SIGTERM
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:2.0];
    while (t.isRunning && [deadline timeIntervalSinceNow] > 0) usleep(50000);
    if (t.isRunning) kill(t.processIdentifier, SIGKILL);
    self.task = nil;
}

@end

// ───────────────── WebView：裁右键项 ＋ 接住 Esc（X-3）─────────────────

@interface PortalWebView : WKWebView
/// Esc 转给门户。壳自己不处理 Esc 的语义（关面板 / 关浮层 / 退编辑 / 关预览 的优先级
/// 全在门户里），只负责把键送过去。
@property (nonatomic, copy) void (^onEscape)(void);
@end

@implementation PortalWebView

/// WKWebView 默认右键菜单里有「重新载入 / 后退 / 前进」，门户是单页应用，这几项
/// 点了只会把状态弄乱。留下拷贝、查找、检查元素这些有用的。
- (void)willOpenMenu:(NSMenu *)menu withEvent:(NSEvent *)event {
    // WKMenuItemIdentifier* 那几个常量在本机 SDK 的 ObjC 头文件里没有导出（只有 Swift 侧有），
    // 所以直接比字面量。哪天 WebKit 改了标识符，最坏结果是这几项没被裁掉，不会出错。
    NSArray *unwanted = @[ @"WKMenuItemIdentifierReload",
                           @"WKMenuItemIdentifierGoBack",
                           @"WKMenuItemIdentifierGoForward" ];
    NSMutableArray *kill = [NSMutableArray array];
    for (NSMenuItem *it in menu.itemArray) {
        if (it.identifier && [unwanted containsObject:it.identifier]) [kill addObject:it];
    }
    for (NSMenuItem *it in kill) [menu removeItem:it];
    [super willOpenMenu:menu withEvent:event];
}

/// X-3：Esc（keyCode 53）在 WKWebView 里到不了网页的 keydown，阅读设置和退出编辑
/// 两处都得靠点按钮。这里截住转给 AMN.escape()。
///
/// performKeyEquivalent: 对所有 keyDown 都会走一遍（默认按钮的回车、取消按钮的 Esc
/// 就是靠这条生效的），不只是带 ⌘ 的组合键，所以 Esc 收得到。
- (BOOL)performKeyEquivalent:(NSEvent *)event {
    if (event.keyCode == 53) {
        // 焦点明确落在别的控件上（比如查找栏的输入框）时不要抢，那边的 Esc 有自己的语义。
        // 焦点是窗口本身（刚开窗、还没点过任何地方）时照样转——不然浮层
        // 会关不掉。查找栏聚焦时第一响应者是它的字段编辑器，是 _findBar 的后代、不是 web
        // 的后代，正好落进这一条。
        NSResponder *fr = self.window.firstResponder;
        if ([fr isKindOfClass:NSView.class] && ![(NSView *)fr isDescendantOf:self]) {
            return [super performKeyEquivalent:event];
        }
        // 输入法正在拼字时 Esc 是「取消这次输入」，不能吞。WKWebView 在 macOS 上
        // 私下实现了 NSTextInputClient（公开头文件里没有），能问到 hasMarkedText 就问；
        // 问不到就放行，宁可少接一次也不要把中文输入弄坏。
        if ([self respondsToSelector:@selector(hasMarkedText)]) {
            id<NSTextInputClient> tic = (id<NSTextInputClient>)self;
            if ([tic hasMarkedText]) return [super performKeyEquivalent:event];
        }
        if (self.onEscape) { self.onEscape(); return YES; }
    }
    return [super performKeyEquivalent:event];
}
@end

// ─────────────────────────── 主程序 ───────────────────────────

@interface AppDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate,
                                   WKScriptMessageHandler, NSToolbarDelegate, NSSearchFieldDelegate,
                                   NSMenuItemValidation, NSToolbarItemValidation,
                                   NSWindowDelegate, NSURLSessionDownloadDelegate, NSURLSessionTaskDelegate>
@end

@implementation AppDelegate {
    NSWindow       *_win;
    PortalWebView  *_web;
    PortalService  *_svc;
    NSURL          *_baseURL;
    BOOL            _loadedOnce;    // 网页至少 didFinish 过一次
    BOOL            _pageReady;     // 门户发过 {type:'ready'}
    BOOL            _shown;         // N-8 首次揭幕已经做过了（一旦置位不再清，窗口开关看 _win.isVisible）

    NSToolbar          *_toolbar;
    NSSearchField      *_searchField;

    NSView         *_findBar;
    NSSearchField  *_findField;
    NSTextField    *_findMsg;

    // AMN.state() 的缓存。validateMenuItem: 只读它，绝不在里面同步等 JS。
    NSDictionary   *_state;
    BOOL            _stateOK;
    NSTimer        *_stateTimer;

    NSURL          *_docURL;        // 当前文档，分享和代理图标用

    id              _ctxMenuId;     // 正在弹的右键菜单的回执 id
    NSMutableArray<NSString *> *_pendingOpens;   // 网页还没就绪时攒下的 openPath
    BOOL            _pendingRescan; // 窗口关着的时候点了「重扫全库」，等门户就绪再补发
    BOOL            _suppressSearchEcho;

    NSStatusItem   *_statusItem;    // 服务常驻期间的唯一入口
    BOOL            _quitting;      // 正在走退出流程，别再起新的定时器
    WKUserContentController *_ucc;  // 拆 web 时要从它上面把 amn handler 摘掉

    NSURLSession              *_updSession;
    NSURLSessionDownloadTask  *_updTask;
    NSWindow                  *_updWin;
    NSProgressIndicator       *_updBar;
    NSTextField               *_updLabel;
    NSDictionary              *_updInfo;         // version / zip / notes / page / digest
    BOOL                       _updBusy;
    BOOL                       _updInteractive;  // 菜单点的才报「已是最新 / 连不上」
    BOOL                       _updCancel;       // 关进度窗之后，后台解压结果必须丢掉
    BOOL                       _installingUpdate;

    /// 独立文稿窗 ＋ 完整浏览器辅窗。**两份都要存**：ARC 下 releasedWhenClosed=NO
    /// 的窗口没人强引用的话，close 之后就直接没了，windowWillClose: 里再去拿就是空的。
    /// 两类窗靠 window.identifier 分（amn.solo / amn.browser）：⌘W 和 Esc 语义不同。
    NSMutableArray<WKWebView *> *_soloWebs;
    NSMutableArray<NSWindow *>  *_soloWins;
}

// MARK: 启动

- (instancetype)init {
    self = [super init];
    if (self) {
        // 设计约束 8：openURLs 可能早于 didFinishLaunching，数组必须先在。
        _pendingOpens = [NSMutableArray array];
        _soloWebs = [NSMutableArray array];
        _soloWins = [NSMutableArray array];
    }
    return self;
}

- (void)applicationDidFinishLaunching:(NSNotification *)n {
    [self buildMenu];
    [self buildWindow];
    [self buildStatusItem];
    if (![self ensureVault]) {
        [NSApp terminate:nil];
        return;
    }
    [self startService];

    // N-8 的兜底：网页要是既不 didFinish 也不报错，到点了照样把窗口放出来
    [NSTimer scheduledTimerWithTimeInterval:kShowTimeout repeats:NO block:^(NSTimer *t) {
        [self showWindowIfNeeded];
    }];
    [self scheduleAutoUpdateCheck];
}

/// 第一次打开还没选过库：主线程弹说明 + 只选文件夹的面板。取消就退出。
- (BOOL)ensureVault {
    NSString *v = locateRoot();
    if (v.length) {
        prepareVault(v);
        return YES;
    }
    return [self chooseVaultRequired:YES];
}

/// required=YES：第一次，取消／点退出都返回 NO，调用方退出 app。
/// required=NO：菜单换库，取消则保持当前库。
- (BOOL)chooseVaultRequired:(BOOL)required {
    if (required) {
        NSAlert *a = [NSAlert new];
        a.messageText = @"选择一个文件夹";
        a.informativeText = @"AM·Note 会索引你选的文件夹。文件留在原地，不会上传到网上。";
        [a addButtonWithTitle:@"选择文件夹"];
        [a addButtonWithTitle:@"退出"];
        if ([a runModal] != NSAlertFirstButtonReturn) return NO;
    }

    NSOpenPanel *p = [NSOpenPanel openPanel];
    p.canChooseFiles = NO;
    p.canChooseDirectories = YES;
    p.allowsMultipleSelection = NO;
    p.canCreateDirectories = YES;
    p.prompt = @"选择文件夹";
    p.message = @"AM·Note 会索引你选的文件夹。文件留在原地，不会上传到网上。";
    NSString *cur = locateRoot();
    if (cur.length) p.directoryURL = [NSURL fileURLWithPath:cur];

    if ([p runModal] != NSModalResponseOK || !p.URL.path.length) return NO;

    NSString *path = normPath(p.URL.path);
    if (!path.length) return NO;
    prepareVault(path);
    saveVault(path);
    return YES;
}

- (void)mChooseVault:(id)s {
    [NSApp activateIgnoringOtherApps:YES];
    NSString *old = locateRoot();
    if (![self chooseVaultRequired:NO]) return;
    NSString *now = locateRoot();
    if (old.length && now.length && [normPath(old) isEqualToString:normPath(now)]) return;

    [_svc stop];
    _svc = nil;
    [self detachWebView];
    [self startService];
}

/// 起服务。失败走 failReason:detail:，那里可以「重试」——重试就是再调一次这个方法。
- (void)startService {
    // 起服务是同步阻塞的，扔后台，别让主线程卡住
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *tools = locateTools();
        if (!tools) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self failReason:@"找不到程序文件，请重新编译或从发布包安装" detail:@""];
            });
            return;
        }
        NSString *vault = locateRoot();
        if (!vault.length) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self failReason:@"还没有选择文件夹"
                          detail:@"请用菜单「库 → 选择文件夹…」选一个文件夹。"];
            });
            return;
        }
        PortalService *svc = [[PortalService alloc] initWithTools:tools vault:vault];
        NSString *err = nil;
        if (![svc startAndReturnError:&err]) {
            dispatch_async(dispatch_get_main_queue(), ^{ [self failWithMessage:err]; });
            return;
        }
        dispatch_async(dispatch_get_main_queue(), ^{ [self serviceReady:svc]; });
    });
}

- (void)serviceReady:(PortalService *)svc {
    _svc = svc;
    _baseURL = [NSURL URLWithString:
        [NSString stringWithFormat:@"http://127.0.0.1:%ld/portal", (long)svc.port]];
    if (!_baseURL) {
        [self failReason:@"端口号不对"
                  detail:[NSString stringWithFormat:@"服务报回来的端口是 %ld。", (long)svc.port]];
        return;
    }
    [self detachWebView];      // 重试时把上一块清掉
    [self attachWebView];
    [self startTimers];
}

/// 建一块 WKWebView 挂进窗口并载入门户。首次启动走这条，关窗后重开也走这条
/// （关窗口把上一块拆掉了，见设计约束 4）。
- (void)attachWebView {
    if (_web || !_baseURL || !_win) return;

    WKWebViewConfiguration *cfg = [WKWebViewConfiguration new];
    // 默认 dataStore 是持久的。门户的阅读设置（字体、字号、行距、栏宽、深浅）存在
    // localStorage 的 tp-pref 里，换成 nonPersistent 每次关掉都会丢。
    cfg.websiteDataStore = WKWebsiteDataStore.defaultDataStore;
    // UA 尾巴给门户认，让它知道自己跑在原生壳里（可以据此收掉「安装到 Dock」那类 PWA 专属入口）
    cfg.applicationNameForUserAgent = @"AMNoteShell/1";

    // 壳标志。门户据此隐藏自己那条搜索栏和已经搬到原生工具栏的按钮。
    // atDocumentStart：门户的初始化代码一跑就能读到，不能晚于它。
    WKUserScript *flag = [[WKUserScript alloc]
        initWithSource:@"window.__AMN_SHELL__ = true; window.__AMN_SHELL_VER__ = 2;"
         injectionTime:WKUserScriptInjectionTimeAtDocumentStart
      forMainFrameOnly:YES];
    [cfg.userContentController addUserScript:flag];
    // userContentController 对 handler 是强引用，这里等于 web → cfg → controller → self
    // 一个环。AppDelegate 本来就活到进程结束（gDelegate 强持有），不额外做弱代理。
    // 但关窗口要把这一整块拆掉，所以留个引用，detachWebView 里从它上面摘 handler——
    // 不要去读 _web.configuration，那是一份 copy，靠它摘是靠实现细节。
    _ucc = cfg.userContentController;
    [_ucc addScriptMessageHandler:self name:@"amn"];

    PortalWebView *w = [[PortalWebView alloc] initWithFrame:_win.contentView.bounds
                                              configuration:cfg];
    w.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    w.navigationDelegate = self;
    w.UIDelegate = self;
    w.allowsBackForwardNavigationGestures = NO;   // 门户是单页，误触两指滑动会把状态弄没
    if (@available(macOS 13.3, *)) { w.inspectable = YES; }  // 调门户要用 Safari 检查器
    __weak typeof(self) weakSelf = self;
    w.onEscape = ^{ [weakSelf amn:@"escape" args:nil]; };

    [_win.contentView addSubview:w positioned:NSWindowBelow relativeTo:_findBar];
    _web = w;
    _loadedOnce = NO;
    _pageReady = NO;
    [self relayout];
    applyThemeToWindow(_win, gAppliedTheme);
    [w loadRequest:[NSURLRequest requestWithURL:_baseURL]];
}

/// 关窗口时把门户整块拆掉。**不是为了省内存**，是为了不让一个看不见的页面
/// 继续用 client=portal 把 Agent 的打开队列取走（设计约束 4）。
/// 顺手把 handler 摘掉，断开 web → cfg → controller → self 那个环。
- (void)detachWebView {
    if (!_web) return;
    [self hideFind:nil];
    [_web stopLoading];
    _web.navigationDelegate = nil;
    _web.UIDelegate = nil;
    _web.onEscape = nil;
    // 独立文稿窗口跟主窗口共用同一个 userContentController（WebKit 要求新窗口用
    // 传进来的那份 configuration）。主窗口关了但还有独立窗口开着时不能摘 handler，
    // 摘了那几个窗口的标题和脏点就不再更新。
    if (!_soloWebs.count) { [_ucc removeScriptMessageHandlerForName:@"amn"]; _ucc = nil; }
    [_web removeFromSuperview];
    _web = nil;

    _loadedOnce = NO;
    _pageReady = NO;
    _state = nil;
    _stateOK = NO;
    _docURL = nil;
    // _pendingOpens 不清：那是「还没送到的打开请求」，窗口重开后照样要送。
    _suppressSearchEcho = YES;
    _searchField.stringValue = @"";
    _suppressSearchEcho = NO;
    _win.title = @"AM·Note";
    _win.representedURL = nil;
    _win.documentEdited = NO;
    if (_toolbar) [_toolbar validateVisibleItems];
}

- (void)startTimers {
    if (!_stateTimer) {
        _stateTimer = [NSTimer scheduledTimerWithTimeInterval:kStateTick repeats:YES
                                                        block:^(NSTimer *t) {
            // 不在前台就别问了，菜单也点不着，省一次 JS 往返
            if (NSApp.isActive) [self refreshState];
        }];
    }
    // 收件箱撤了，Dock 徽标跟着撤——一个永远消不掉的红点比没有还糟
    NSApp.dockTile.badgeLabel = nil;
}

// MARK: N-9 错误框

/// 第一行当标题，其余当正文。服务那边报错时就是按 "原因\n\n细节" 拼的。
- (void)failWithMessage:(NSString *)msg {
    NSString *m = msg.length ? msg : @"未知错误";
    NSRange sep = [m rangeOfString:@"\n\n"];
    if (sep.location != NSNotFound) {
        [self failReason:[m substringToIndex:sep.location]
                  detail:[m substringFromIndex:sep.location + 2]];
    } else {
        NSArray *lines = [m componentsSeparatedByString:@"\n"];
        [self failReason:lines.firstObject
                  detail:lines.count > 1 ? [[lines subarrayWithRange:NSMakeRange(1, lines.count - 1)]
                                            componentsJoinedByString:@"\n"] : @""];
    }
}

/// 三个出路：重试（重新走一遍启动流程）、拷贝错误信息、退出。
/// 拷完不退出，回到同一个框——拷贝是为了发给别人问，不是为了结束。
- (void)failReason:(NSString *)reason detail:(NSString *)detail {
    NSString *full = detail.length ? [NSString stringWithFormat:@"%@\n\n%@", reason, detail] : reason;
    while (YES) {
        NSAlert *a = [NSAlert new];
        a.alertStyle = NSAlertStyleCritical;
        a.messageText = reason.length ? reason : @"AM·Note 起不来";
        a.informativeText = detail.length ? detail : @"";
        [a addButtonWithTitle:@"重试"];
        [a addButtonWithTitle:@"拷贝错误信息"];
        [a addButtonWithTitle:@"退出"];
        NSModalResponse r = [a runModal];
        if (r == NSAlertFirstButtonReturn) {
            [_svc stop];
            _svc = nil;
            [self startService];
            return;
        }
        if (r == NSAlertSecondButtonReturn) {
            [NSPasteboard.generalPasteboard clearContents];
            [NSPasteboard.generalPasteboard setString:full forType:NSPasteboardTypeString];
            continue;
        }
        [NSApp terminate:nil];
        return;
    }
}

// MARK: 窗口

- (void)buildWindow {
    _win = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, kWinW, kWinH)
                                       styleMask:(NSWindowStyleMaskTitled |
                                                  NSWindowStyleMaskFullSizeContentView |
                                                  NSWindowStyleMaskClosable |
                                                  NSWindowStyleMaskMiniaturizable |
                                                  NSWindowStyleMaskResizable)
                                         backing:NSBackingStoreBuffered
                                           defer:NO];
    _win.title = @"AM·Note";              // Mission Control / 辅助功能仍读得到
    _win.identifier = @"amn.main";
    _win.titleVisibility = NSWindowTitleHidden;  // 不占标签那一行
    _win.titlebarAppearsTransparent = YES;
    _win.titlebarSeparatorStyle = NSTitlebarSeparatorStyleNone;
    _win.movableByWindowBackground = YES;
    _win.hasShadow = YES;
    _win.minSize = NSMakeSize(kMinW, kMinH);
    // 关了不释放。窗口对象要留着，重开时只是把它 order 回来。
    _win.releasedWhenClosed = NO;
    _win.delegate = self;                 // windowShouldClose: / windowWillClose:
    _win.tabbingMode = NSWindowTabbingModeDisallowed;
    [_win setFrameAutosaveName:@"AMNoteMainWindow"];

    NSView *content = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, kWinW, kWinH)];
    content.wantsLayer = YES;
    _win.contentView = content;

    applySeamlessChrome(_win);
    [self buildFindBar];

    if (NSEqualPoints(_win.frame.origin, NSZeroPoint)) [_win center];
    // N-8：这里不显示窗口。等服务起来、网页 didFinish 了再放出来。
}

/// N-8：内容就绪才显示窗口。只管首次那一下，重复调用无害。
- (void)showWindowIfNeeded {
    if (_shown || !_win) return;
    [self openMainWindow];
}

/// 把窗口开起来。没有 web（关窗时拆掉了）就现建一块，重新载入门户。
/// 状态栏菜单、点 Dock 图标、点通知、双击一份 md 都走这条。
- (void)openMainWindow {
    if (!_win || _quitting) return;
    if (!_web) [self attachWebView];
    _shown = YES;
    [_win makeKeyAndOrderFront:nil];
    applySeamlessChrome(_win);
    [NSApp activateIgnoringOtherApps:YES];
}

/// 关窗口会把门户卸掉，未保存的改动就没了，所以这里跟 ⌘Q 一样拦一道。
/// dirty 只读缓存（documentEdited 由门户的 dirty 消息维护，state 每 0.5 秒刷一次），
/// 不在这里同步等 JS——windowShouldClose: 是同步返回的，等不起。
- (BOOL)windowShouldClose:(NSWindow *)sender {
    if (sender == _updWin) {
        _updCancel = YES;
        [_updTask cancel];
        _updTask = nil;
        _updBusy = NO;
        return YES;
    }
    // 独立文稿窗口：能编辑就可能有没存的改动，跟主窗口一样拦一道，只是话短一点
    if (sender != _win) {
        if (!sender.documentEdited) return YES;
        NSAlert *a = [NSAlert new];
        a.alertStyle = NSAlertStyleWarning;
        a.messageText = @"有改动还没保存";
        a.informativeText = @"关掉这个窗口，正在编辑的改动会丢掉。";
        [a addButtonWithTitle:@"回去保存"];
        NSButton *close = [a addButtonWithTitle:@"直接关闭"];
        if (@available(macOS 11.0, *)) { close.hasDestructiveAction = YES; }
        return [a runModal] == NSAlertSecondButtonReturn;
    }
    if (!_web || !_loadedOnce) return YES;
    if (!_win.documentEdited && ![self stateFlag:@"dirty"]) return YES;

    NSAlert *a = [NSAlert new];
    a.alertStyle = NSAlertStyleWarning;
    a.messageText = @"有改动还没保存";
    a.informativeText = @"关掉窗口会把门户卸下来，正在编辑的稿子会丢掉改动。服务和状态栏图标照常留着。";
    [a addButtonWithTitle:@"回去保存"];
    NSButton *close = [a addButtonWithTitle:@"直接关闭"];
    if (@available(macOS 11.0, *)) { close.hasDestructiveAction = YES; }
    return [a runModal] == NSAlertSecondButtonReturn;
}

/// 关窗口只关窗口：服务和状态栏图标都留着，只把门户卸掉。
- (void)windowWillClose:(NSNotification *)n {
    if (n.object == _updWin) {
        _updWin = nil;
        _updBar = nil;
        _updLabel = nil;
        return;
    }
    if (n.object != _win && ![_soloWins containsObject:n.object]) return;
    // 独立文稿窗口：拆掉它那块 webview 就完事。**不摘 amn handler**——
    // 那是跟主窗口共用的一个，摘了主窗口的标题和脏点就不再更新。
    if (n.object != _win) {
        NSWindow *win = n.object;
        for (WKWebView *w in [_soloWebs copy]) {
            if (w.window != win) continue;
            [w stopLoading];
            w.navigationDelegate = nil;
            w.UIDelegate = nil;
            if ([w isKindOfClass:PortalWebView.class]) ((PortalWebView *)w).onEscape = nil;
            [w removeFromSuperview];
            [_soloWebs removeObject:w];
        }
        win.delegate = nil;
        [_soloWins removeObject:win];
        return;
    }
    if (_quitting) return;
    [self detachWebView];
    // _shown 不清。它只管 N-8 那次「等内容就绪再揭幕」，清了的话启动 12 秒后
    // 那条兜底定时器会把刚关掉的窗口又弹回来。
}

/// 全屏会重建标题栏层级；等系统完成这一帧、以及动画结束后再刷两遍。
/// 只刷分隔线不够：系统会重新塞进一层偏白的 vibrancy，必须连底色一起重上。
- (void)windowDidEnterFullScreen:(NSNotification *)n {
    NSWindow *window = n.object;
    dispatch_async(dispatch_get_main_queue(), ^{ applySeamlessChrome(window); });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.12 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{ applySeamlessChrome(window); });
}

- (void)windowDidExitFullScreen:(NSNotification *)n {
    NSWindow *window = n.object;
    dispatch_async(dispatch_get_main_queue(), ^{ applySeamlessChrome(window); });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.12 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{ applySeamlessChrome(window); });
}

/// 菜单校验读的是上一扇窗的缓存。切到辅窗立刻按 ⌘W，会把别人的 isStart 用在这里。
- (void)windowDidBecomeKey:(NSNotification *)n {
    if (n.object == _win || [_soloWins containsObject:n.object]) [self refreshState];
}

// MARK: 状态栏图标
//
// 服务常驻之后，没窗口的时候这里是唯一入口。菜单项一律不给快捷键：
// 状态栏菜单跟主菜单是两套，给了键只会跟门户抢（设计约束 1 的老坑）。

- (void)buildStatusItem {
    _statusItem = [NSStatusBar.systemStatusBar statusItemWithLength:NSSquareStatusItemLength];
    NSStatusBarButton *b = _statusItem.button;
    NSImage *img = menubarCloudIcon();
    if (img) {
        b.image = img;                // template，跟着菜单栏深浅走
    } else {
        b.title = @"AM";
    }
    b.toolTip = @"AM·Note";
    // behavior 保持默认：不给 TerminationOnRemoval。手滑把图标拖出菜单栏
    // 不该把常驻的服务一起收掉。

    NSMenu *m = [[NSMenu alloc] initWithTitle:@"AM·Note"];
    // 这条菜单要在 app 不活跃的时候也点得动，不走 validateMenuItem:，全部常亮。
    m.autoenablesItems = NO;
    [[m addItemWithTitle:@"打开窗口" action:@selector(mOpenWindow:) keyEquivalent:@""] setTarget:self];
    [m addItem:NSMenuItem.separatorItem];
    [[m addItemWithTitle:@"检查更新…" action:@selector(mCheckUpdate:) keyEquivalent:@""] setTarget:self];
    [m addItem:NSMenuItem.separatorItem];
    [[m addItemWithTitle:@"重扫全库" action:@selector(rescan:) keyEquivalent:@""] setTarget:self];
    [[m addItemWithTitle:@"服务信息" action:@selector(serviceInfo:) keyEquivalent:@""] setTarget:self];
    [m addItem:NSMenuItem.separatorItem];
    [[m addItemWithTitle:@"退出 AM·Note" action:@selector(mQuit:) keyEquivalent:@""] setTarget:self];
    _statusItem.menu = m;
}

- (void)mOpenWindow:(id)s { [self openMainWindow]; }
- (void)mQuit:(id)s       { [NSApp terminate:nil]; }

/// 点 Dock 图标。窗口是我们自己 order out 的，不实现这条 AppKit 不会把它放回来。
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)vis {
    if (!vis) [self openMainWindow];
    return YES;
}

// MARK: N-1 工具栏

- (void)buildToolbar {
    // 不再赋给 _win.toolbar：网页自己画顶栏，原生工具栏会把交通灯顶出那 52px。
    // 方法留着，搜索框那条 AMN.search 的线没拆，自定义面板要拖回来还编译得过。
    _toolbar = [[NSToolbar alloc] initWithIdentifier:@"AMNoteMain5"];
    _toolbar.delegate = self;
    _toolbar.displayMode = NSToolbarDisplayModeIconOnly;
    _toolbar.allowsUserCustomization = YES;
    _toolbar.autosavesConfiguration = YES;
    _toolbar.showsBaselineSeparator = NO;
    _toolbar.sizeMode = NSToolbarSizeModeSmall;
}

/// 设计约束 5：默认就这三件。搜索框不在默认里，但留在「允许」里——
/// 她想把它拖回来还是拖得回来，代码那条 AMN.search 的线也一直连着。
- (NSArray<NSToolbarItemIdentifier> *)toolbarDefaultItemIdentifiers:(NSToolbar *)t {
    return @[ kTBLists,
              NSToolbarFlexibleSpaceItemIdentifier,
              kTBNew ];
}

- (NSArray<NSToolbarItemIdentifier> *)toolbarAllowedItemIdentifiers:(NSToolbar *)t {
    return @[ kTBLists, kTBSearch, kTBNew,
              NSToolbarFlexibleSpaceItemIdentifier, NSToolbarSpaceItemIdentifier ];
}

/// 图标项。自绘圆角图标；画不出来时退到 SF Symbol，再没有就退成文字。
- (NSToolbarItem *)iconItem:(NSToolbarItemIdentifier)ident
                       kind:(NSString *)kind
                     symbol:(NSString *)symbol
                      label:(NSString *)label
                     action:(SEL)action {
    NSToolbarItem *it = [[NSToolbarItem alloc] initWithItemIdentifier:ident];
    it.label = label;
    it.paletteLabel = label;
    it.toolTip = label;
    it.target = self;
    it.action = action;
    NSImage *img = roundToolbarIcon(kind);
    if (!img) img = sym(symbol, label);
    if (img) {
        img.size = NSMakeSize(16, 16);
        img.template = YES;
        NSButton *btn = [[NSButton alloc] initWithFrame:NSMakeRect(0, 0, 28, 22)];
        btn.image = img;
        btn.imagePosition = NSImageOnly;
        btn.imageScaling = NSImageScaleNone;
        btn.bordered = NO;
        btn.bezelStyle = NSBezelStyleShadowlessSquare;
        btn.target = self;
        btn.action = action;
        [btn setButtonType:NSButtonTypeMomentaryChange];
        btn.translatesAutoresizingMaskIntoConstraints = NO;
        [btn.widthAnchor constraintEqualToConstant:28].active = YES;
        [btn.heightAnchor constraintEqualToConstant:22].active = YES;
        it.view = btn;
        if (@available(macOS 10.15, *)) { it.bordered = NO; }
    } else {
        it.title = label;
    }
    return it;
}

- (NSToolbarItem *)toolbar:(NSToolbar *)t
     itemForItemIdentifier:(NSToolbarItemIdentifier)ident
 willBeInsertedIntoToolbar:(BOOL)flag {
    if ([ident isEqualToString:kTBLists])
        return [self iconItem:ident kind:@"sidebar" symbol:@"sidebar.left" label:@"列表"
                       action:@selector(tbLists:)];
    if ([ident isEqualToString:kTBNew])
        return [self iconItem:ident kind:@"plus" symbol:@"plus" label:@"新建随手记"
                       action:@selector(tbNew:)];

    if ([ident isEqualToString:kTBSearch]) {
        if (@available(macOS 11.0, *)) {
            NSSearchToolbarItem *it = [[NSSearchToolbarItem alloc] initWithItemIdentifier:ident];
            it.label = @"搜索";
            it.paletteLabel = @"搜索";
            it.resignsFirstResponderWithCancel = YES;
            it.searchField.placeholderString = @"搜索全库";
            it.searchField.delegate = self;
            it.searchField.sendsWholeSearchString = NO;   // 边打边搜，跟门户自己那条一致
            _searchField = it.searchField;
            return it;
        }
        NSToolbarItem *it = [[NSToolbarItem alloc] initWithItemIdentifier:ident];
        NSSearchField *f = [[NSSearchField alloc] initWithFrame:NSMakeRect(0, 0, 240, 24)];
        f.placeholderString = @"搜索全库";
        f.delegate = self;
        it.view = f;
        it.label = @"搜索";
        _searchField = f;
        return it;
    }

    return nil;
}

/// 剩下的三件都不做启用态校验：列表切换和新建在任何状态下都该点得着，
/// 搜索框自己管自己。
- (BOOL)validateToolbarItem:(NSToolbarItem *)item {
    return YES;
}

- (void)tbLists:(id)s { [self amn:@"toggleLists" args:nil]; }
- (void)tbNew:(id)s   { [self amn:@"newNote" args:nil]; }

// MARK: 原生搜索框

- (void)controlTextDidChange:(NSNotification *)n {
    if (n.object != _searchField) return;          // 查找栏那个搜索框不走这条
    if (_suppressSearchEcho) return;
    [self amn:@"search" args:@[_searchField.stringValue ?: @""]];
}

- (BOOL)control:(NSControl *)control textView:(NSTextView *)tv doCommandBySelector:(SEL)sel {
    if (control == _searchField && sel == @selector(insertNewline:)) {
        [self amn:@"searchEnter" args:nil];
        return YES;
    }
    // 查找栏里按 Esc 是关查找栏，不是清空搜索词（搜索框的默认行为），也不该传给网页
    if (control == _findField && sel == @selector(cancelOperation:)) {
        [self hideFind:nil];
        return YES;
    }
    return NO;
}

// MARK: X-19 查找栏
//
// 页内查找。⌘K 是跳到全库搜索框不是页内查找，⌘F 门户没占，拿来做这个。
// 外观按系统查找栏来：材质底、搜索框、上一个／下一个用分段控件、右侧「完成」。

- (void)buildFindBar {
    NSView *content = _win.contentView;
    CGFloat w = content.bounds.size.width;

    NSVisualEffectView *bar = [[NSVisualEffectView alloc] initWithFrame:
        NSMakeRect(0, content.bounds.size.height - kChromeH - kFindBarH, w, kFindBarH)];
    bar.material = NSVisualEffectMaterialHeaderView;
    bar.blendingMode = NSVisualEffectBlendingModeWithinWindow;
    bar.state = NSVisualEffectStateActive;
    bar.autoresizingMask = NSViewWidthSizable | NSViewMinYMargin;
    bar.hidden = YES;
    _findBar = bar;

    NSBox *line = [[NSBox alloc] initWithFrame:NSMakeRect(0, 0, w, 1)];
    line.boxType = NSBoxSeparator;
    line.autoresizingMask = NSViewWidthSizable;
    [bar addSubview:line];

    _findField = [[NSSearchField alloc] initWithFrame:NSMakeRect(12, 7, 260, 24)];
    _findField.placeholderString = @"在本页查找";
    _findField.target = self;
    _findField.action = @selector(findNext:);
    _findField.delegate = self;          // 只为接住 Esc，见 doCommandBySelector:
    [bar addSubview:_findField];

    NSImage *up = sym(@"chevron.up", @"上一个"), *down = sym(@"chevron.down", @"下一个");
    NSSegmentedControl *nav;
    if (up && down) {
        nav = [NSSegmentedControl segmentedControlWithImages:@[up, down]
                                                trackingMode:NSSegmentSwitchTrackingMomentary
                                                      target:self action:@selector(findNav:)];
    } else {
        nav = [NSSegmentedControl segmentedControlWithLabels:@[@"上一个", @"下一个"]
                                                trackingMode:NSSegmentSwitchTrackingMomentary
                                                      target:self action:@selector(findNav:)];
    }
    nav.frame = NSMakeRect(282, 6, 88, 26);
    nav.segmentStyle = NSSegmentStyleSeparated;
    [bar addSubview:nav];

    _findMsg = [NSTextField labelWithString:@""];
    _findMsg.frame = NSMakeRect(382, 10, 220, 18);
    _findMsg.font = [NSFont systemFontOfSize:11];
    _findMsg.textColor = NSColor.secondaryLabelColor;
    [bar addSubview:_findMsg];

    NSButton *close = [NSButton buttonWithTitle:@"完成" target:self action:@selector(hideFind:)];
    close.frame = NSMakeRect(w - 80, 6, 68, 26);
    close.bezelStyle = NSBezelStyleRounded;
    close.autoresizingMask = NSViewMinXMargin;
    [bar addSubview:close];

    [content addSubview:bar];
}

- (void)relayout {
    NSView *content = _win.contentView;
    CGFloat h = content.bounds.size.height, w = content.bounds.size.width;
    CGFloat top = _findBar.hidden ? 0 : kFindBarH;
    _findBar.frame = NSMakeRect(0, h - kChromeH - kFindBarH, w, kFindBarH);
    _web.frame = NSMakeRect(0, 0, w, h - top);
}

- (void)showFind:(id)s {
    _findBar.hidden = NO;
    [self relayout];
    [_win makeFirstResponder:_findField];
}

- (void)hideFind:(id)s {
    _findBar.hidden = YES;
    _findMsg.stringValue = @"";
    [self relayout];
    [_win makeFirstResponder:_web];
}

- (void)findNav:(NSSegmentedControl *)seg { [self findBackwards:(seg.selectedSegment == 0)]; }
- (void)findNext:(id)s { [self findBackwards:NO]; }
- (void)findPrev:(id)s { [self findBackwards:YES]; }

- (void)findBackwards:(BOOL)back {
    NSString *q = _findField.stringValue;
    if (!q.length) { _findMsg.stringValue = @""; return; }
    WKFindConfiguration *c = [WKFindConfiguration new];
    c.backwards = back;
    c.caseSensitive = NO;
    c.wraps = YES;
    [_web findString:q withConfiguration:c completionHandler:^(WKFindResult *r) {
        // 找不到的两种常见原因：内容在没激活的 pane 里（display:none），或者在 iframe 里
        self->_findMsg.stringValue = r.matchFound ? @"" : @"没找到";
    }];
}

// MARK: 壳 → 门户

/// 菜单和快捷键打到「当前这块网页」上。辅窗在前台时还走 _web，
/// 就会在看不见的主窗里新建标签、关错页。
- (WKWebView *)activeWeb {
    NSWindow *key = NSApp.keyWindow;
    if (!key || key == _win) return _web;
    NSUInteger i = [_soloWins indexOfObject:key];
    if (i != NSNotFound && i < _soloWebs.count) return _soloWebs[i];
    return _web;
}

/// 独立阅读窗：没有标签带，⌘W / Esc 都是关窗口。完整浏览器辅窗不是。
- (BOOL)isSoloWindow:(NSWindow *)win {
    if (!win || win == _win) return NO;
    if ([win.identifier isEqualToString:@"amn.solo"]) return YES;
    if ([win.identifier isEqualToString:@"amn.browser"]) return NO;
    NSUInteger i = [_soloWins indexOfObject:win];
    if (i == NSNotFound) return NO;
    WKWebView *web = i < _soloWebs.count ? _soloWebs[i] : nil;
    if ([self isAuxURL:web.URL]) return NO;
    return YES;
}

/// 所有 AMN.* 调用统一走这里。三层防御：网页没起来不发；window.AMN 不存在不发；
/// 函数不存在不发。老门户装进新壳里只会是「按了没反应」，不会报错。
- (void)amn:(NSString *)fn args:(NSArray *)args {
    WKWebView *web = [self activeWeb];
    if (!web || !fn.length) return;
    if (web == _web && !_loadedOnce) return;
    NSString *argStr = @"";
    if (args.count) {
        NSData *d = [NSJSONSerialization dataWithJSONObject:args options:0 error:nil];
        NSString *s = d ? [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] : nil;
        if (s.length >= 2) argStr = [s substringWithRange:NSMakeRange(1, s.length - 2)];
    }
    NSString *js = [NSString stringWithFormat:
        @"try{if(window.AMN&&typeof AMN.%@==='function')AMN.%@(%@)}catch(e){}", fn, fn, argStr];
    [web evaluateJavaScript:js completionHandler:nil];
}

// MARK: 菜单校验用的状态缓存

- (void)refreshState {
    WKWebView *web = [self activeWeb];
    if (!web) return;
    if (web == _web && !_loadedOnce) return;
    [web evaluateJavaScript:
        @"(function(){try{if(window.AMN&&typeof AMN.state==='function')return AMN.state()}catch(e){}return ''})()"
           completionHandler:^(id r, NSError *e) {
        NSDictionary *d = nil;
        if ([r isKindOfClass:NSDictionary.class]) {
            d = r;                                   // 门户直接返回对象也认
        } else if ([r isKindOfClass:NSString.class] && [r length]) {
            id o = [NSJSONSerialization JSONObjectWithData:
                        [r dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];
            if ([o isKindOfClass:NSDictionary.class]) d = o;
        }
        self->_state = d;
        self->_stateOK = (d != nil);
        [self syncFromState];
    }];
}

- (BOOL)stateFlag:(NSString *)key {
    if (!_stateOK) {
        // state() 拿不到时一律当假，只有两条例外：
        // hasDoc 可以看 doc 消息；书签栏默认开着，缺字段时勾号不该先灭掉。
        if ([key isEqualToString:@"hasDoc"]) return _docURL != nil;
        if ([key isEqualToString:@"bookmarks"]) return YES;
        return NO;
    }
    id v = _state[key];
    if (v == nil && [key isEqualToString:@"bookmarks"]) return YES;
    return [v respondsToSelector:@selector(boolValue)] ? [v boolValue] : NO;
}

- (void)syncFromState {
    if (_toolbar) [_toolbar validateVisibleItems];
}

- (void)applicationDidBecomeActive:(NSNotification *)n { [self refreshState]; }

// MARK: 门户 → 壳（amn 消息通道）

- (void)userContentController:(WKUserContentController *)ucc
      didReceiveScriptMessage:(WKScriptMessage *)message {
    if (![message.body isKindOfClass:NSDictionary.class]) return;
    NSDictionary *m = message.body;
    NSString *type = [m[@"type"] isKindOfClass:NSString.class] ? m[@"type"] : nil;
    if (!type.length) return;

    // 独立文稿窗口跟主窗口共用同一个 handler，靠发件的那块 webview 分。
    // 这一岔必须在 ready 之前：ready 会把 Agent 排的「打开这份」冲给发件那一页，
    // 让独立窗口收了就等于把主窗口的收件吃掉。
    if (message.webView && message.webView != _web) {
        [self soloMessage:m from:message.webView];
        return;
    }

    if ([type isEqualToString:@"ready"]) {
        _pageReady = YES;
        [self flushPending];
        [self refreshState];
        return;
    }
    if ([type isEqualToString:@"theme"]) {
        NSString *theme = [m[@"value"] isKindOfClass:NSString.class] ? m[@"value"] : @"auto";
        applyThemeToWindow(_win, theme);
        applySeamlessChrome(_win);
        return;
    }
    if ([type isEqualToString:@"doc"]) {
        NSString *title = [m[@"title"] isKindOfClass:NSString.class] ? m[@"title"] : nil;
        NSString *path  = [m[@"path"]  isKindOfClass:NSString.class] ? m[@"path"]  : nil;
        _win.title = title.length ? title : @"AM·Note";
        _docURL = path.length ? [NSURL fileURLWithPath:path] : nil;
        _win.representedURL = _docURL;      // N-4 代理图标：⌘ 点标题看路径、拖标题栏发文件
        [self refreshState];
        return;
    }
    if ([type isEqualToString:@"dirty"]) {
        _win.documentEdited = [m[@"on"] boolValue];
        [self refreshState];
        return;
    }
    if ([type isEqualToString:@"canedit"]) {
        [self refreshState];
        return;
    }
    if ([type isEqualToString:@"search"]) {
        NSString *t = [m[@"text"] isKindOfClass:NSString.class] ? m[@"text"] : @"";
        _suppressSearchEcho = YES;          // 设 stringValue 不触发 controlTextDidChange，这里只是加一道保险
        _searchField.stringValue = t;
        _suppressSearchEcho = NO;
        return;
    }
    if ([type isEqualToString:@"confirm"]) { [self showConfirm:m]; return; }
    if ([type isEqualToString:@"menu"])    { [self showContextMenu:m]; return; }
    if ([type isEqualToString:@"clip"]) {
        NSString *t = [m[@"text"] isKindOfClass:NSString.class] ? m[@"text"] : nil;
        [self copyToPasteboard:t];
        return;
    }
    if ([type isEqualToString:@"share"]) {
        NSString *p = [m[@"path"] isKindOfClass:NSString.class] ? m[@"path"] : nil;
        [self sharePath:p];
        return;
    }
}

/// 拷贝路径。剪贴板是界面的事，写在壳里：NSPasteboard 收的是 NSString，
/// 不起子进程，也不经过任何编码转换。
///
/// **验剪贴板内容别用 `pbpaste`。** 这台机器的 `~/.CFUserTextEncoding` 是
/// `0x2`（MacChineseTrad），`pbpaste` 输出时按它转码，明明是好的 UTF-8
/// 也会打印成 Big5 乱码。20260825 因为这个误判过一次「pbcopy 写坏了」，
/// 实际写进去的一直是对的。要验就用 `[NSPasteboard stringForType:]` 读回来。
- (void)copyToPasteboard:(NSString *)text {
    if (!text.length) return;
    [NSPasteboard.generalPasteboard clearContents];
    [NSPasteboard.generalPasteboard setString:text forType:NSPasteboardTypeString];
}

// MARK: X-20 确认框

- (void)showConfirm:(NSDictionary *)m {
    NSAlert *a = [NSAlert new];
    a.messageText = [m[@"title"] isKindOfClass:NSString.class] ? m[@"title"] : @"确认";
    a.informativeText = [m[@"body"] isKindOfClass:NSString.class] ? m[@"body"] : @"";
    NSArray *btns = [m[@"buttons"] isKindOfClass:NSArray.class] ? m[@"buttons"] : nil;
    if (!btns.count) btns = @[ @"好", @"取消" ];
    for (id b in btns) [a addButtonWithTitle:[b description]];

    // 破坏性那一项标红（macOS 11+），取消那一项挂上 Esc
    id destructive = m[@"destructive"], cancel = m[@"cancel"];
    if (@available(macOS 11.0, *)) {
        if ([destructive isKindOfClass:NSNumber.class]) {
            NSInteger i = [destructive integerValue];
            if (i >= 0 && i < (NSInteger)a.buttons.count) a.buttons[i].hasDestructiveAction = YES;
        }
    }
    if ([cancel isKindOfClass:NSNumber.class]) {
        NSInteger i = [cancel integerValue];
        if (i >= 0 && i < (NSInteger)a.buttons.count) a.buttons[i].keyEquivalent = @"\033";
    }

    id rid = m[@"id"];
    [a beginSheetModalForWindow:_win completionHandler:^(NSModalResponse r) {
        NSInteger idx = r - NSAlertFirstButtonReturn;
        [self amn:@"confirmReply" args:@[ rid ?: NSNull.null, @(idx) ]];
    }];
}

// MARK: X-6 右键菜单

- (void)showContextMenu:(NSDictionary *)m {
    NSArray *items = [m[@"items"] isKindOfClass:NSArray.class] ? m[@"items"] : nil;
    id rid = m[@"id"];
    if (!items.count || !_web) {
        [self amn:@"menuPick" args:@[ rid ?: NSNull.null, NSNull.null ]];
        return;
    }

    NSMenu *menu = [[NSMenu alloc] initWithTitle:@""];
    menu.autoenablesItems = NO;
    for (id raw in items) {
        if (![raw isKindOfClass:NSDictionary.class]) continue;
        NSDictionary *it = raw;
        if ([it[@"sep"] boolValue]) { [menu addItem:NSMenuItem.separatorItem]; continue; }
        NSString *label = [it[@"label"] isKindOfClass:NSString.class] ? it[@"label"] : @"";
        NSMenuItem *mi = [[NSMenuItem alloc] initWithTitle:label
                                                    action:@selector(ctxPick:) keyEquivalent:@""];
        mi.target = self;
        mi.representedObject = it[@"id"];
        mi.enabled = it[@"enabled"] ? [it[@"enabled"] boolValue] : YES;
        if ([it[@"checked"] boolValue]) mi.state = NSControlStateValueOn;
        [menu addItem:mi];
    }

    // 坐标：门户给的是 CSS 像素、webView 左上为原点。先按页面缩放折算成点。
    // y 只有在视图不是 flipped（原点在左下）时才需要翻过来 —— WKWebView 在本机
    // 是 flipped 的，无条件翻会把菜单弹到上下镜像的位置，所以按 isFlipped 判。
    CGFloat zoom = 1.0;
    if (@available(macOS 14.0, *)) { zoom = _web.pageZoom > 0 ? _web.pageZoom : 1.0; }
    CGFloat x = [m[@"x"] doubleValue] * zoom;
    CGFloat y = [m[@"y"] doubleValue] * zoom;
    CGFloat yy = _web.isFlipped ? y : _web.bounds.size.height - y;
    NSPoint pt = NSMakePoint(x, yy);

    _ctxMenuId = rid;
    [menu popUpMenuPositioningItem:nil atLocation:pt inView:_web];

    // 选中的那条会在 ctxPick: 里把 _ctxMenuId 清掉。等一拍再看，还在就是取消了。
    // 等这一拍是因为 NSMenu 的 action 不保证在 popUp 返回之前送达。
    id mid = rid;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.05 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (self->_ctxMenuId && [self->_ctxMenuId isEqual:mid]) {
            self->_ctxMenuId = nil;
            [self amn:@"menuPick" args:@[ mid ?: NSNull.null, NSNull.null ]];
        }
    });
}

- (void)ctxPick:(NSMenuItem *)mi {
    id mid = _ctxMenuId;
    _ctxMenuId = nil;
    [self amn:@"menuPick" args:@[ mid ?: NSNull.null, mi.representedObject ?: NSNull.null ]];
}

// MARK: 分享

- (void)sharePath:(NSString *)path {
    NSURL *u = path.length ? [NSURL fileURLWithPath:path] : _docURL;
    if (!u) return;
    NSSharingServicePicker *p = [[NSSharingServicePicker alloc] initWithItems:@[ u ]];
    // 锚在当前那个窗口上。独立文稿窗口里点分享，浮层不该跳到主窗口去
    NSWindow *host = NSApp.keyWindow ?: _win;
    NSView *anchor = host.contentView;
    // 锚点落在窗口顶部靠右。工具栏那颗分享 20260829 撤了（设计约束 5），
    // 现在门户里点的分享、文件菜单里点的分享，走的都是这一条。
    NSRect r = NSMakeRect(NSMaxX(anchor.bounds) - 120, NSMaxY(anchor.bounds) - 1, 1, 1);
    [p showRelativeToRect:r ofView:anchor preferredEdge:NSMinYEdge];
}

// MARK: N-10 / N-11 文档类型与 URL scheme

- (void)application:(NSApplication *)app openURLs:(NSArray<NSURL *> *)urls {
    for (NSURL *u in urls) {
        NSString *rel = [self relPathForURL:u];
        if (rel.length) [self queueOpen:rel];
    }
}

/// 旧入口兜底。实现了 openURLs 的系统不会再走这里；留下是防止
/// `open -a` 某些路径只送 openFile。
- (BOOL)application:(NSApplication *)app openFile:(NSString *)filename {
    NSString *rel = [self relPathForURL:[NSURL fileURLWithPath:filename]];
    if (rel.length) [self queueOpen:rel];
    return YES;
}

/// 双击的 md、amnote://open?path=… 都归到「库内相对路径」这一种形态，交给 AMN.openPath。
/// 库外的绝对路径原样留下，deliverOpen: 会改走 AMN.openExternalPath。
- (NSString *)relPathForURL:(NSURL *)u {
    NSString *path = nil;
    if (u.isFileURL) {
        NSURL *file = u.filePathURL ?: u;
        path = file.path;
    } else if ([u.scheme.lowercaseString isEqualToString:@"amnote"]) {
        NSURLComponents *c = [NSURLComponents componentsWithURL:u resolvingAgainstBaseURL:NO];
        for (NSURLQueryItem *q in c.queryItems) {
            if ([q.name isEqualToString:@"path"]) { path = q.value; break; }
        }
    }
    if (!path.length) return nil;
    path = normPath(path);
    if (![path hasPrefix:@"/"]) return path;                 // 已经是相对路径
    NSString *root = normPath(locateRoot());
    if (root.length) {
        NSString *prefix = [root stringByAppendingString:@"/"];
        if ([path hasPrefix:prefix]) return [path substringFromIndex:prefix.length];
        if ([path isEqualToString:root]) return @"";
    }
    return path;                                             // 库外的路径原样递过去
}

- (void)deliverOpen:(NSString *)rel {
    if (!rel.length) return;
    if ([rel hasPrefix:@"/"]) [self amn:@"openExternalPath" args:@[rel]];
    else [self amn:@"openPath" args:@[rel]];
}

- (void)queueOpen:(NSString *)rel {
    if (!rel.length) return;
    if (_pageReady && _loadedOnce) { [self deliverOpen:rel]; return; }
    if (!_pendingOpens) _pendingOpens = [NSMutableArray array];
    if (![_pendingOpens containsObject:rel]) [_pendingOpens addObject:rel];
    // 窗口关着的时候双击一份 md、或者 amnote:// 进来，得把窗口开回来，
    // 不然这条就一直躺在队列里没人取。窗口还没建好时 openMainWindow 自己会退。
    if (_win && !_win.isVisible) [self openMainWindow];
}

/// 门户刚就绪时补发攒下的动作：打开哪几份、以及那一次没赶上的重扫。
/// 网页还没就绪时绝对不能把队列倒掉：amn: 这时会静默丢掉。
- (void)flushPending {
    if (!_web || !_loadedOnce || !_pageReady) return;
    if (_pendingOpens.count) {
        NSArray *pend = [_pendingOpens copy];
        [_pendingOpens removeAllObjects];
        for (NSString *rel in pend) [self deliverOpen:rel];
    }
    if (_pendingRescan) {
        _pendingRescan = NO;
        [self amn:@"rescan" args:nil];
    }
}

// MARK: 菜单
//
// X-12～X-18。所有指向网页的项都 target 到 self，方法体里 evaluateJavaScript 调 AMN.*，
// 启用态一律读 AMN.state() 的缓存（见 validateMenuItem:），不在校验里同步等 JS。

- (void)buildMenu {
    NSMenu *main = [NSMenu new];

    // ── 应用 ──
    NSMenuItem *appItem = [NSMenuItem new];
    NSMenu *app = [NSMenu new];
    [app addItemWithTitle:@"关于 AM·Note"
                   action:@selector(orderFrontStandardAboutPanel:) keyEquivalent:@""];  // X-17
    [[app addItemWithTitle:@"检查更新…" action:@selector(mCheckUpdate:) keyEquivalent:@""] setTarget:self];
    NSMenuItem *autoUp = [app addItemWithTitle:@"自动检查更新"
                                        action:@selector(mToggleAutoUpdate:)
                                 keyEquivalent:@""];
    autoUp.target = self;
    [app addItem:NSMenuItem.separatorItem];
    [[app addItemWithTitle:@"设置…" action:@selector(mSettings:) keyEquivalent:@","] setTarget:self];
    [app addItem:NSMenuItem.separatorItem];
    NSMenuItem *svcMenuItem = [app addItemWithTitle:@"服务" action:NULL keyEquivalent:@""];
    NSMenu *sysServices = [[NSMenu alloc] initWithTitle:@"服务"];
    svcMenuItem.submenu = sysServices;
    NSApp.servicesMenu = sysServices;
    [app addItem:NSMenuItem.separatorItem];
    [app addItemWithTitle:@"隐藏 AM·Note" action:@selector(hide:) keyEquivalent:@"h"];
    NSMenuItem *ho = [app addItemWithTitle:@"隐藏其他"
                                    action:@selector(hideOtherApplications:) keyEquivalent:@"h"];
    ho.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
    [app addItemWithTitle:@"全部显示" action:@selector(unhideAllApplications:) keyEquivalent:@""];
    [app addItem:NSMenuItem.separatorItem];
    [app addItemWithTitle:@"退出 AM·Note" action:@selector(terminate:) keyEquivalent:@"q"];
    appItem.submenu = app;
    [main addItem:appItem];

    // ── 文件（X-13）──
    NSMenuItem *fileItem = [NSMenuItem new];
    NSMenu *file = [[NSMenu alloc] initWithTitle:@"文件"];
    [[file addItemWithTitle:@"新建窗口" action:@selector(mNew:) keyEquivalent:@"n"] setTarget:self];
    [[file addItemWithTitle:@"新建标签页" action:@selector(mNewTab:) keyEquivalent:@"t"] setTarget:self];
    [[file addItemWithTitle:@"新建随手记" action:@selector(mNewNote:) keyEquivalent:@""] setTarget:self];
    [file addItem:NSMenuItem.separatorItem];
    NSMenuItem *popw = [file addItemWithTitle:@"在新窗口打开"
                                       action:@selector(mPopoutWindow:) keyEquivalent:@"o"];
    popw.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
    popw.target = self;
    NSMenuItem *reveal = [file addItemWithTitle:@"在访达中显示"
                                         action:@selector(mReveal:) keyEquivalent:@"r"];
    reveal.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    reveal.target = self;
    NSMenuItem *cpath = [file addItemWithTitle:@"拷贝路径"
                                        action:@selector(mCopyPath:) keyEquivalent:@"c"];
    cpath.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
    cpath.target = self;
    // 工具栏那颗分享撤了（设计约束 5），能力落在这里。不给快捷键：分享是低频动作，
    // 占一个键不划算，也少一次跟门户抢键的机会。
    [[file addItemWithTitle:@"分享…" action:@selector(mShare:) keyEquivalent:@""] setTarget:self];
    [file addItem:NSMenuItem.separatorItem];
    // 移到废纸篓。⌘⌫ 是 Finder 里同一件事的键，肌肉记忆是现成的。
    // **编辑态一定要灰掉**（validateMenuItem: 读 canTrash，门户在编辑态返回假）：
    // 菜单项灰着，这一下 ⌘⌫ 才会放行给 WebView，在编辑器里仍然是「删到行首」。
    NSMenuItem *trash = [file addItemWithTitle:@"移到废纸篓"
                                        action:@selector(mTrash:)
                                 keyEquivalent:[NSString stringWithFormat:@"%C",
                                                (unichar)NSBackspaceCharacter]];
    trash.keyEquivalentModifierMask = NSEventModifierFlagCommand;
    trash.target = self;
    [file addItem:NSMenuItem.separatorItem];
    // ⌘E 进编辑。菜单占了这个键，门户自己那条 ⌘E 监听就作废了（设计约束 1），
    // 一切以 AMN.enterEdit 为准——「光标落到鼠标所在那一段」的判断在门户里做。
    [[file addItemWithTitle:@"进入编辑" action:@selector(mEnterEdit:) keyEquivalent:@"e"] setTarget:self];
    [[file addItemWithTitle:@"存储" action:@selector(mSave:) keyEquivalent:@"s"] setTarget:self];
    [file addItem:NSMenuItem.separatorItem];
    [[file addItemWithTitle:@"关闭" action:@selector(mCloseTab:) keyEquivalent:@"w"] setTarget:self];
    NSMenuItem *closeWin = [file addItemWithTitle:@"关闭窗口"
                                           action:@selector(performClose:) keyEquivalent:@"w"];
    closeWin.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    [file addItem:NSMenuItem.separatorItem];
    [[file addItemWithTitle:@"打印…" action:@selector(mPrint:) keyEquivalent:@"p"] setTarget:self];
    fileItem.submenu = file;
    [main addItem:fileItem];

    // ── 编辑：不加这些项，门户里的 MD 编辑器没法复制粘贴撤销 ──
    NSMenuItem *editItem = [NSMenuItem new];
    NSMenu *edit = [[NSMenu alloc] initWithTitle:@"编辑"];
    [edit addItemWithTitle:@"撤销" action:@selector(undo:) keyEquivalent:@"z"];
    NSMenuItem *redo = [edit addItemWithTitle:@"重做" action:@selector(redo:) keyEquivalent:@"z"];
    redo.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    [edit addItem:NSMenuItem.separatorItem];
    [edit addItemWithTitle:@"剪切" action:@selector(cut:) keyEquivalent:@"x"];
    [edit addItemWithTitle:@"拷贝" action:@selector(copy:) keyEquivalent:@"c"];
    [edit addItemWithTitle:@"粘贴" action:@selector(paste:) keyEquivalent:@"v"];
    [edit addItemWithTitle:@"全选" action:@selector(selectAll:) keyEquivalent:@"a"];
    [edit addItem:NSMenuItem.separatorItem];
    // ⌘K 从「跳到搜索框」改成「快速直达」浮层（设计约束 1）。原来那个动作让位到 ⌥⌘K。
    [[edit addItemWithTitle:@"快速直达" action:@selector(mQuickOpen:) keyEquivalent:@"k"] setTarget:self];
    NSMenuItem *focusQ = [edit addItemWithTitle:@"跳到搜索框"
                                         action:@selector(mFocusSearch:) keyEquivalent:@"k"];
    focusQ.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
    focusQ.target = self;
    [[edit addItemWithTitle:@"聚焦地址栏" action:@selector(mFocusOmnibox:) keyEquivalent:@"l"] setTarget:self];
    [edit addItem:NSMenuItem.separatorItem];
    [[edit addItemWithTitle:@"在本页查找" action:@selector(showFind:) keyEquivalent:@"f"] setTarget:self];
    editItem.submenu = edit;
    [main addItem:editItem];

    // ── 显示 ──
    NSMenuItem *viewItem = [NSMenuItem new];
    NSMenu *view = [[NSMenu alloc] initWithTitle:@"显示"];
    [[view addItemWithTitle:@"后退" action:@selector(mBack:) keyEquivalent:@"["] setTarget:self];
    [[view addItemWithTitle:@"前进" action:@selector(mForward:) keyEquivalent:@"]"] setTarget:self];
    [view addItem:NSMenuItem.separatorItem];
    NSMenuItem *bm = [view addItemWithTitle:@"显示书签栏"
                                     action:@selector(mToggleBookmarks:) keyEquivalent:@"b"];
    bm.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    bm.target = self;
    // 改名「显示列表」：新版切的是「纯正文 ↔ 边栏＋列表＋正文」整块，不只是最左那一栏。
    NSMenuItem *sb = [view addItemWithTitle:@"显示列表"
                                     action:@selector(mLists:) keyEquivalent:@"s"];
    sb.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagControl;
    sb.target = self;
    NSMenuItem *toc = [view addItemWithTitle:@"显示大纲"
                                      action:@selector(mToc:) keyEquivalent:@"i"];
    toc.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
    toc.target = self;
    [view addItem:NSMenuItem.separatorItem];
    [[view addItemWithTitle:@"随手记" action:@selector(mNotes:) keyEquivalent:@""] setTarget:self];
    [[view addItemWithTitle:@"最近" action:@selector(mRecent:) keyEquivalent:@""] setTarget:self];
    [view addItem:NSMenuItem.separatorItem];
    [[view addItemWithTitle:@"重新载入" action:@selector(reloadPortal:) keyEquivalent:@"r"] setTarget:self];
    [view addItem:NSMenuItem.separatorItem];
    [[view addItemWithTitle:@"放大" action:@selector(zoomIn:) keyEquivalent:@"+"] setTarget:self];
    [[view addItemWithTitle:@"缩小" action:@selector(zoomOut:) keyEquivalent:@"-"] setTarget:self];
    [[view addItemWithTitle:@"实际大小" action:@selector(zoomReset:) keyEquivalent:@"0"] setTarget:self];
    [view addItem:NSMenuItem.separatorItem];
    NSMenuItem *fs = [view addItemWithTitle:@"进入全屏"
                                     action:@selector(toggleFullScreen:) keyEquivalent:@"f"];
    fs.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagControl;
    viewItem.submenu = view;
    [main addItem:viewItem];

    // ── 库（X-15：原来叫「服务」，跟系统那个 Services 撞名）──
    NSMenuItem *libItem = [NSMenuItem new];
    NSMenu *lib = [[NSMenu alloc] initWithTitle:@"库"];
    [[lib addItemWithTitle:@"选择文件夹…" action:@selector(mChooseVault:) keyEquivalent:@""] setTarget:self];
    [lib addItem:NSMenuItem.separatorItem];
    [[lib addItemWithTitle:@"重扫全库" action:@selector(rescan:) keyEquivalent:@""] setTarget:self];
    [[lib addItemWithTitle:@"在浏览器里打开门户" action:@selector(openInBrowser:) keyEquivalent:@""] setTarget:self];
    [lib addItem:NSMenuItem.separatorItem];
    [[lib addItemWithTitle:@"打开工具目录" action:@selector(openTools:) keyEquivalent:@""] setTarget:self];
    [[lib addItemWithTitle:@"服务信息" action:@selector(serviceInfo:) keyEquivalent:@""] setTarget:self];
    libItem.submenu = lib;
    [main addItem:libItem];

    // ── 窗口 ──
    NSMenuItem *winItem = [NSMenuItem new];
    NSMenu *win = [[NSMenu alloc] initWithTitle:@"窗口"];
    // ⇧⌘W 关掉窗口之后 app 还活着（服务常驻），主菜单里得有一条能把窗口叫回来。
    // 不给快捷键：设计约束 1，别跟门户抢键。
    [[win addItemWithTitle:@"打开窗口" action:@selector(mOpenWindow:) keyEquivalent:@""] setTarget:self];
    [win addItem:NSMenuItem.separatorItem];
    [win addItemWithTitle:@"最小化" action:@selector(performMiniaturize:) keyEquivalent:@"m"];
    [win addItemWithTitle:@"缩放" action:@selector(performZoom:) keyEquivalent:@""];
    [win addItem:NSMenuItem.separatorItem];
    [win addItemWithTitle:@"前置全部窗口" action:@selector(arrangeInFront:) keyEquivalent:@""];
    winItem.submenu = win;
    [main addItem:winItem];
    NSApp.windowsMenu = win;

    // ── 帮助（X-16）──
    NSMenuItem *helpItem = [NSMenuItem new];
    NSMenu *help = [[NSMenu alloc] initWithTitle:@"帮助"];
    [[help addItemWithTitle:@"AM·Note 帮助" action:@selector(mHelp:) keyEquivalent:@"?"] setTarget:self];
    [[help addItemWithTitle:@"快捷键一览" action:@selector(mKeys:) keyEquivalent:@""] setTarget:self];
    helpItem.submenu = help;
    [main addItem:helpItem];
    NSApp.helpMenu = help;

    NSApp.mainMenu = main;
}

/// 只读缓存，不等 JS。缓存没值（老门户没导出 AMN）时，这些项一律灰着——
/// 按不动比按了没反应好。
- (BOOL)validateMenuItem:(NSMenuItem *)item {
    SEL a = item.action;
    if (a == @selector(mSave:))     return [self stateFlag:@"editing"] || [self stateFlag:@"dirty"];
    if (a == @selector(mCloseTab:)) {
        NSWindow *key = NSApp.keyWindow;
        BOOL closeWin = NO;
        if ([self isSoloWindow:key] ||
            (key && key != _win && ![_soloWins containsObject:key])) closeWin = YES;
        else if (!_stateOK) closeWin = ![self stateFlag:@"inReader"];
        else closeWin = [self stateFlag:@"isStart"];
        item.title = closeWin ? @"关闭窗口" : @"关闭标签";
        return (key != nil);
    }
    // 独立窗口在前台时灰掉：它自己就是一个独立窗口，再开一个还是同一份
    if (a == @selector(mPopoutWindow:))
        return [self stateFlag:@"hasDoc"] && (NSApp.keyWindow == _win || NSApp.keyWindow == nil);
    if (a == @selector(mReveal:))   return [self stateFlag:@"hasDoc"];
    if (a == @selector(mShare:))    return [self stateFlag:@"hasDoc"];
    // 老门户没有 canTrash 这个键，stateFlag: 返回 NO，⌘⌫ 就一路放行给网页——
    // 灰掉比「按了把别人的字删了」好
    if (a == @selector(mTrash:))    return [self stateFlag:@"canTrash"];
    if (a == @selector(mEnterEdit:)) return [self stateFlag:@"canEdit"];
    if (a == @selector(mToc:)) {
        item.state = [self stateFlag:@"toc"] ? NSControlStateValueOn : NSControlStateValueOff;
        return [self stateFlag:@"hasDoc"];
    }
    if (a == @selector(mLists:)) {
        // 勾号先认新键 lists，认不到退回老键 sidebar——改版期间两版门户都可能装在这个壳里，
        // 认错了最坏也只是勾号不亮，菜单照样点得动。
        BOOL on = [self stateFlag:@"lists"] || [self stateFlag:@"sidebar"];
        item.state = on ? NSControlStateValueOn : NSControlStateValueOff;
        return _stateOK;
    }
    if (a == @selector(mToggleBookmarks:)) {
        item.state = [self stateFlag:@"bookmarks"] ? NSControlStateValueOn : NSControlStateValueOff;
        return _stateOK;
    }
    if (a == @selector(mBack:))     return [self stateFlag:@"canBack"];
    if (a == @selector(mForward:))  return [self stateFlag:@"canForward"];
    if (a == @selector(mCopyPath:)) return [self stateFlag:@"hasDoc"];
    if (a == @selector(mNew:) || a == @selector(mNewTab:) ||
        a == @selector(mNewNote:) ||
        a == @selector(mSettings:) ||
        a == @selector(mNotes:) || a == @selector(mRecent:) ||
        a == @selector(mQuickOpen:) || a == @selector(mFocusSearch:) ||
        a == @selector(mFocusOmnibox:)) {
        return _stateOK;
    }
    if (a == @selector(mPrint:) || a == @selector(reloadPortal:) ||
        a == @selector(zoomIn:) || a == @selector(zoomOut:) || a == @selector(zoomReset:)) {
        return _web != nil && _loadedOnce;
    }
    if (a == @selector(rescan:) || a == @selector(openInBrowser:) || a == @selector(serviceInfo:)) {
        return _svc.port > 0;
    }
    if (a == @selector(mCheckUpdate:)) return !_updBusy;
    if (a == @selector(mToggleAutoUpdate:)) {
        item.state = amnAutoCheckOn() ? NSControlStateValueOn : NSControlStateValueOff;
        return YES;
    }
    return YES;
}

// MARK: 菜单动作 · 转给门户

- (void)mNew:(id)s       { [self amn:@"newWindow" args:nil]; }
- (void)mNewTab:(id)s    { [self amn:@"newTab" args:nil]; }
- (void)mNewNote:(id)s   { [self amn:@"newNote" args:nil]; }

/// 把主窗口里开着的那一份放进一个独立窗口。门户自己 window.open，
/// 绕回上面 createWebViewWithConfiguration: 那一支。
- (void)mPopoutWindow:(id)s { [self amn:@"popoutWindow" args:nil]; }

/// 门户没导出 AMN 时，这一条壳自己也能做——路径在 doc 消息里已经拿到了
- (void)mReveal:(id)s {
    if (!_stateOK && _docURL) {
        [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[_docURL]];
        return;
    }
    [self amn:@"reveal" args:nil];
}
- (void)mSave:(id)s      { [self amn:@"save" args:nil]; }
- (void)mCloseTab:(id)s {
    NSWindow *key = NSApp.keyWindow;
    // 独立阅读窗没有标签；更新进度这类附属窗也不是浏览器。两者都直接关。
    if (key && key != _win &&
        ([self isSoloWindow:key] || ![_soloWins containsObject:key])) {
        [key performClose:nil];
        return;
    }
    // 主窗和完整浏览器辅窗：仅剩起始页时关窗口，否则只关当前标签。
    // 门户没导出 AMN 时沿用旧判据（有没有文稿），免得 ⌘W 在首页变成按了没反应。
    if (!_stateOK) {
        if ([self stateFlag:@"inReader"]) [self amn:@"closeTab" args:nil];
        else [(key ?: _win) performClose:nil];
        return;
    }
    if ([self stateFlag:@"isStart"]) [(key ?: _win) performClose:nil];
    else [self amn:@"closeTab" args:nil];
}
- (void)mSettings:(id)s  { [self amn:@"settings" args:nil]; }
- (void)mLists:(id)s     { [self amn:@"toggleLists" args:nil]; }
- (void)mToc:(id)s       { [self amn:@"toggleToc" args:nil]; }
- (void)mNotes:(id)s     { [self amn:@"openNotes" args:nil]; }
- (void)mRecent:(id)s    { [self amn:@"openRecent" args:nil]; }
/// ⌘K。老门户没有 quickOpen 这个函数，amn: 那三层防御会让它静默不响应，不会报错。
- (void)mQuickOpen:(id)s  { [self amn:@"quickOpen" args:nil]; }
- (void)mFocusSearch:(id)s { [self amn:@"focusSearch" args:nil]; }
- (void)mFocusOmnibox:(id)s { [self amn:@"focusOmnibox" args:nil]; }
- (void)mToggleBookmarks:(id)s { [self amn:@"toggleBookmarks" args:nil]; }
- (void)mBack:(id)s      { [self amn:@"back" args:nil]; }
- (void)mForward:(id)s   { [self amn:@"forward" args:nil]; }
- (void)mEnterEdit:(id)s  { [self amn:@"enterEdit" args:nil]; }
/// 分享。门户没导出 AMN 时壳自己也能做——路径在 doc 消息里已经拿到了。
- (void)mShare:(id)s     { [self sharePath:nil]; }
/// 移到废纸篓。壳不自己搬文件：搬哪一份、搬完关哪几个标签、撤销那颗按钮，
/// 全在门户里，壳只转发一下。门户那边不弹确认框，废纸篓本身就是后悔药。
- (void)mTrash:(id)s     { [self amn:@"trashDoc" args:nil]; }
/// 拷贝路径：门户把路径交给壳，壳写 NSPasteboard（见 copyToPasteboard: 上面那段）。
/// 门户没导出 AMN 时壳自己也能做——路径在 doc 消息里已经拿到了。
- (void)mCopyPath:(id)s {
    if (!_stateOK && _docURL) { [self copyToPasteboard:_docURL.absoluteString]; return; }
    [self amn:@"copyPath" args:nil];
}

// MARK: 菜单动作 · 原生

- (void)mPrint:(id)s {
    if (!_web) return;
    if (@available(macOS 11.0, *)) {
        NSPrintOperation *op = [_web printOperationWithPrintInfo:NSPrintInfo.sharedPrintInfo];
        op.showsPrintPanel = YES;
        op.view.frame = _web.bounds;
        [op runOperationModalForWindow:_win delegate:nil didRunSelector:NULL contextInfo:NULL];
    }
}

- (void)reloadPortal:(id)s { [_web reload]; }

- (void)zoomIn:(id)s  { if (@available(macOS 14.0, *)) _web.pageZoom = MIN(_web.pageZoom + 0.1, 3.0); }
- (void)zoomOut:(id)s { if (@available(macOS 14.0, *)) _web.pageZoom = MAX(_web.pageZoom - 0.1, 0.5); }
- (void)zoomReset:(id)s { if (@available(macOS 14.0, *)) _web.pageZoom = 1.0; }

/// 重扫全库一律交给门户（AMN.rescan 自己带着口令发 POST）。
/// **壳这边不再直接打 /__rescan**：改版后那条路由是 POST ＋ X-AMN-Token 门禁，
/// 而 token 只注进页面、壳手上没有，照老样子 GET 过去只会换回一个 405。
/// 从状态栏菜单点、而窗口正关着的时候：先把窗口开回来，等门户就绪了再补发这一次。
- (void)rescan:(id)s {
    if (_web && _loadedOnce) { [self amn:@"rescan" args:nil]; return; }
    if (_svc.port <= 0) return;
    _pendingRescan = YES;
    [self openMainWindow];
}

- (void)openInBrowser:(id)s { if (_baseURL) [NSWorkspace.sharedWorkspace openURL:_baseURL]; }

- (void)openTools:(id)s {
    NSString *t = locateTools();
    if (t) [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:t]];
}

- (void)serviceInfo:(id)s {
    // 从状态栏菜单点进来时 app 可能不在前台，不先激活的话这个 modal 会藏在别人后面
    [NSApp activateIgnoringOtherApps:YES];
    NSString *body = [NSString stringWithFormat:
        @"端口：%@\n进程：%@\n窗口：%@\n工具目录：%@\n解释器：%@",
        _svc.port > 0 ? [NSString stringWithFormat:@"%ld", (long)_svc.port] : @"还没起来",
        !_svc ? @"未启动"
              : (_svc.adopted ? @"复用外部实例"
                              : [NSString stringWithFormat:@"PID %d", _svc.task.processIdentifier]),
        _win.isVisible ? @"开着" : @"关着（服务常驻，双击一份 md 会自己弹回来）",
        locateTools() ?: @"未找到",
        pickPython() ?: @"无"];
    NSAlert *a = [NSAlert new];
    a.messageText = @"服务信息";
    a.informativeText = body;
    [a addButtonWithTitle:@"好"];
    [a addButtonWithTitle:@"拷贝"];
    if ([a runModal] == NSAlertSecondButtonReturn) {
        [NSPasteboard.generalPasteboard clearContents];
        [NSPasteboard.generalPasteboard setString:body forType:NSPasteboardTypeString];
    }
}

- (void)mHelp:(id)s {
    NSString *readme = [NSBundle.mainBundle.resourcePath
                        stringByAppendingPathComponent:@"README.md"];
    if (readme.length && [NSFileManager.defaultManager fileExistsAtPath:readme]) {
        [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:readme]];
        return;
    }
    [self mKeys:s];
}

- (void)mKeys:(id)s {
    NSAlert *a = [NSAlert new];
    a.messageText = @"快捷键";
    a.informativeText =
        @"⌘T 新建标签页（开始页）\n⌘N 新建窗口\n⌘L 聚焦地址栏\n⌘⇧B 显示/隐藏书签栏\n"
         "⌘[ / ⌘] 后退 / 前进\n⌥⌘C 拷贝路径\n⇧⌘R 在访达中显示\n⌥⌘O 把这份放到独立阅读窗\n"
         "⌘E 进入编辑\n⌘S 存储\n⌘⌫ 移到废纸篓\n⌘W 关闭标签／仅剩起始页时关窗口\n"
         "⇧⌘W 关闭窗口\n⌘P 打印\n"
         "⌃Tab 切换标签\n"
         "⌘K 快速直达\n⌥⌘K 聚焦地址栏\n⌃⌘S 显示列表\n⌥⌘I 显示大纲\n"
         "⌘F 在本页查找\n⌘R 重新载入\n"
         "⌃⌘F 进入全屏\nEsc 关浮层 / 退出编辑";
    [a addButtonWithTitle:@"好"];
    [a runModal];
}

// MARK: 更新（GitHub Releases）
//
// 查版本用 /releases/latest；API 不通就跟网页跳转拿 tag。装包只认 AMNote-mac.zip。
// 正在跑的二进制不能自己覆盖自己，所以真正替换交给退出后的 bash 脚本。

- (void)mToggleAutoUpdate:(id)s {
    [NSUserDefaults.standardUserDefaults setBool:!amnAutoCheckOn() forKey:kAutoCheckKey];
}

- (void)mCheckUpdate:(id)s {
    [NSApp activateIgnoringOtherApps:YES];
    [self checkForUpdateInteractive:YES];
}

- (void)scheduleAutoUpdateCheck {
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    NSInteger n = [d integerForKey:kLaunchCountKey] + 1;
    [d setInteger:n forKey:kLaunchCountKey];
    if (!amnAutoCheckOn()) return;
    if (n < 2) return;        // 第一次打开别弹，跟 Sparkle 同一份客气
    NSDate *last = [d objectForKey:kLastCheckKey];
    if ([last isKindOfClass:NSDate.class] &&
        [[NSDate date] timeIntervalSinceDate:last] < kUpdateEvery) return;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(kUpdateDelay * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (_quitting || _updBusy || !amnAutoCheckOn()) return;
        [self checkForUpdateInteractive:NO];
    });
}

- (void)checkForUpdateInteractive:(BOOL)interactive {
    if (_updBusy) {
        if (interactive && _updWin) [_updWin makeKeyAndOrderFront:nil];
        return;
    }
    _updBusy = YES;
    _updInteractive = interactive;
    _updCancel = NO;
    [NSUserDefaults.standardUserDefaults setObject:[NSDate date] forKey:kLastCheckKey];

    NSString *api = [NSString stringWithFormat:
                     @"https://api.github.com/repos/%@/releases/latest", kUpdateRepo];
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:api]];
    [req setValue:amnUserAgent() forHTTPHeaderField:@"User-Agent"];
    [req setValue:@"application/vnd.github+json" forHTTPHeaderField:@"Accept"];
    req.timeoutInterval = 20;

    __weak AppDelegate *weak = self;
    [[NSURLSession.sharedSession dataTaskWithRequest:req
        completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
        dispatch_async(dispatch_get_main_queue(), ^{
            NSHTTPURLResponse *http = ([resp isKindOfClass:NSHTTPURLResponse.class]
                                       ? (NSHTTPURLResponse *)resp : nil);
            if (!err && http.statusCode == 200 && data.length) {
                [weak parseUpdateAPIData:data];
                return;
            }
            [weak fallbackLatestTag];
        });
    }] resume];
}

- (void)parseUpdateAPIData:(NSData *)data {
    id obj = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![obj isKindOfClass:NSDictionary.class]) { [self fallbackLatestTag]; return; }
    NSDictionary *d = obj;
    NSString *tag = d[@"tag_name"];
    if (![tag isKindOfClass:NSString.class] || !tag.length) { [self fallbackLatestTag]; return; }

    NSURL *zip = nil;
    NSString *digest = nil;
    NSNumber *size = nil;
    id assets = d[@"assets"];
    if ([assets isKindOfClass:NSArray.class]) {
        for (id a in (NSArray *)assets) {
            if (![a isKindOfClass:NSDictionary.class]) continue;
            if (![a[@"name"] isEqualToString:kUpdateAsset]) continue;
            NSString *u = a[@"browser_download_url"];
            if (![u isKindOfClass:NSString.class]) continue;
            NSURL *url = [NSURL URLWithString:u];
            if (!amnURLTrusted(url)) continue;
            zip = url;
            if ([a[@"digest"] isKindOfClass:NSString.class]) digest = a[@"digest"];
            if ([a[@"size"] isKindOfClass:NSNumber.class]) size = a[@"size"];
            break;
        }
    }
    if (!zip) {
        zip = [NSURL URLWithString:
               [NSString stringWithFormat:@"https://github.com/%@/releases/download/%@/%@",
                kUpdateRepo, tag, kUpdateAsset]];
        if (!amnURLTrusted(zip)) zip = nil;
    }
    NSString *page = [d[@"html_url"] isKindOfClass:NSString.class] ? d[@"html_url"] : nil;
    NSString *notes = [d[@"body"] isKindOfClass:NSString.class] ? d[@"body"] : @"";
    [self handleRemoteVersion:tag zip:zip page:page notes:notes digest:digest size:size];
}

/// API 被限流或墙了：跟着 /releases/latest 的跳转读 tag，没有说明也没有 digest。
- (void)fallbackLatestTag {
    NSURL *u = [NSURL URLWithString:
                [NSString stringWithFormat:@"https://github.com/%@/releases/latest", kUpdateRepo]];
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:u];
    [req setValue:amnUserAgent() forHTTPHeaderField:@"User-Agent"];
    req.timeoutInterval = 20;
    __weak AppDelegate *weak = self;
    [[NSURLSession.sharedSession dataTaskWithRequest:req
        completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
        dispatch_async(dispatch_get_main_queue(), ^{
            NSURL *final = resp.URL;
            NSString *tag = nil;
            if (final && !err) {
                NSArray *parts = final.pathComponents;
                NSUInteger i = [parts indexOfObject:@"tag"];
                if (i != NSNotFound && i + 1 < parts.count) tag = parts[i + 1];
            }
            if (!tag.length) {
                [weak updateFail:@"连不上 GitHub。可以一会儿再试，或到仓库 Releases 手动下载。"];
                return;
            }
            NSURL *zip = [NSURL URLWithString:
                          [NSString stringWithFormat:@"https://github.com/%@/releases/download/%@/%@",
                           kUpdateRepo, tag, kUpdateAsset]];
            NSString *page = [NSString stringWithFormat:@"https://github.com/%@/releases/tag/%@",
                              kUpdateRepo, tag];
            [weak handleRemoteVersion:tag zip:zip page:page notes:@"" digest:nil size:nil];
        });
    }] resume];
}

- (void)handleRemoteVersion:(NSString *)tag zip:(NSURL *)zip page:(NSString *)page
                      notes:(NSString *)notes digest:(NSString *)digest size:(NSNumber *)size {
    if (amnCmpVersion(tag, amnShortVersion()) <= 0) {
        [self updateUpToDate];
        return;
    }
    if (!zip || !amnURLTrusted(zip)) {
        [self updateFail:@"GitHub 上这个版本没有 mac 安装包（AMNote-mac.zip）。"];
        return;
    }
    NSString *ver = amnStripVer(tag);
    NSMutableDictionary *info = [@{
        @"version": ver,
        @"tag": tag,
        @"zip": zip.absoluteString,
        @"page": page.length ? page :
            [NSString stringWithFormat:@"https://github.com/%@/releases/tag/%@", kUpdateRepo, tag],
        @"notes": amnPlainNotes(notes) ?: @""
    } mutableCopy];
    if (digest.length) info[@"digest"] = digest;
    if (size) info[@"size"] = size;
    _updInfo = info;

    if (!_updInteractive) {
        NSString *skip = [NSUserDefaults.standardUserDefaults stringForKey:kSkipVerKey];
        if (skip.length && amnCmpVersion(skip, ver) == 0) {
            _updBusy = NO;
            return;
        }
    }
    [self offerUpdate:info];
}

- (void)updateUpToDate {
    _updBusy = NO;
    if (!_updInteractive) return;
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *a = [NSAlert new];
    a.messageText = @"已是最新版本";
    a.informativeText = [NSString stringWithFormat:@"当前是 %@。", amnShortVersion()];
    [a addButtonWithTitle:@"好"];
    [a runModal];
}

- (void)updateFail:(NSString *)msg {
    _updBusy = NO;
    [self closeUpdateProgress];
    if (!_updInteractive) return;
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *a = [NSAlert new];
    a.messageText = @"现在检查不了更新";
    a.informativeText = msg.length ? msg : @"连不上 GitHub。";
    [a addButtonWithTitle:@"好"];
    if (_updInfo[@"page"]) [a addButtonWithTitle:@"打开下载页"];
    NSModalResponse r = [a runModal];
    if (r == NSAlertSecondButtonReturn) [self openUpdatePage];
}

- (void)openUpdatePage {
    NSString *p = _updInfo[@"page"];
    if (!p.length)
        p = [NSString stringWithFormat:@"https://github.com/%@/releases/latest", kUpdateRepo];
    NSURL *u = [NSURL URLWithString:p];
    if (u) [NSWorkspace.sharedWorkspace openURL:u];
}

- (void)offerUpdate:(NSDictionary *)info {
    [NSApp activateIgnoringOtherApps:YES];
    NSString *ver = info[@"version"] ?: @"";
    NSMutableString *body = [NSMutableString stringWithFormat:
                             @"现在是 %@。安装会替换当前的 AM·Note，装完自动打开。\n笔记还在原来的文件夹里，不会被动。",
                             amnShortVersion()];
    NSString *notes = info[@"notes"];
    if (notes.length) [body appendFormat:@"\n\n%@", notes];

    NSAlert *a = [NSAlert new];
    a.messageText = [NSString stringWithFormat:@"有新版本 %@", ver];
    a.informativeText = body;
    [a addButtonWithTitle:@"安装更新"];
    [a addButtonWithTitle:@"稍后"];
    [a addButtonWithTitle:@"跳过此版本"];

    void (^done)(NSModalResponse) = ^(NSModalResponse r) {
        if (r == NSAlertFirstButtonReturn) {
            [self startUpdateDownload];
            return;
        }
        if (r == NSAlertThirdButtonReturn && ver.length) {
            [NSUserDefaults.standardUserDefaults setObject:ver forKey:kSkipVerKey];
        }
        _updBusy = NO;
    };

    if (_win.isVisible) {
        [a beginSheetModalForWindow:_win completionHandler:done];
    } else {
        done([a runModal]);
    }
}

- (void)closeUpdateProgress {
    if (!_updWin) return;
    NSWindow *w = _updWin;
    _updWin = nil;
    _updBar = nil;
    _updLabel = nil;
    w.delegate = nil;
    [w close];
}

- (void)showUpdateProgress:(NSString *)text {
    if (!_updWin) {
        NSRect r = NSMakeRect(0, 0, 400, 128);
        _updWin = [[NSWindow alloc] initWithContentRect:r
                                              styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable)
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
        _updWin.title = @"更新 AM·Note";
        _updWin.releasedWhenClosed = NO;
        _updWin.delegate = self;
        _updWin.level = NSFloatingWindowLevel;

        NSView *c = _updWin.contentView;
        _updLabel = [[NSTextField alloc] initWithFrame:NSMakeRect(20, 72, 360, 22)];
        _updLabel.editable = NO;
        _updLabel.bordered = NO;
        _updLabel.drawsBackground = NO;
        _updLabel.font = [NSFont systemFontOfSize:13];
        [c addSubview:_updLabel];

        _updBar = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(20, 44, 360, 16)];
        _updBar.style = NSProgressIndicatorStyleBar;
        _updBar.indeterminate = YES;
        _updBar.minValue = 0;
        _updBar.maxValue = 100;
        [c addSubview:_updBar];
        [_updBar startAnimation:nil];

        NSButton *cancel = [[NSButton alloc] initWithFrame:NSMakeRect(300, 12, 80, 24)];
        cancel.title = @"取消";
        cancel.bezelStyle = NSBezelStyleRounded;
        cancel.target = self;
        cancel.action = @selector(mCancelUpdate:);
        cancel.keyEquivalent = @"\033";
        [c addSubview:cancel];
        [_updWin center];
    }
    _updLabel.stringValue = text ?: @"正在下载…";
    [_updWin makeKeyAndOrderFront:nil];
}

- (void)mCancelUpdate:(id)s {
    [_updWin performClose:nil];
}

- (NSURLSession *)updateSession {
    if (_updSession) return _updSession;
    NSURLSessionConfiguration *cfg = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    cfg.timeoutIntervalForRequest = 30;
    cfg.timeoutIntervalForResource = 600;
    cfg.HTTPAdditionalHeaders = @{ @"User-Agent": amnUserAgent() };
    _updSession = [NSURLSession sessionWithConfiguration:cfg
                                               delegate:self
                                          delegateQueue:NSOperationQueue.mainQueue];
    return _updSession;
}

- (void)startUpdateDownload {
    _updCancel = NO;
    NSString *zip = _updInfo[@"zip"];
    NSURL *url = [NSURL URLWithString:zip];
    if (!amnURLTrusted(url)) {
        [self updateFail:@"下载地址不是 GitHub，已中止。"];
        return;
    }
    NSString *ver = _updInfo[@"version"] ?: @"";
    [self showUpdateProgress:[NSString stringWithFormat:@"正在下载 %@…", ver]];
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:url];
    [req setValue:amnUserAgent() forHTTPHeaderField:@"User-Agent"];
    req.timeoutInterval = 60;
    _updTask = [[self updateSession] downloadTaskWithRequest:req];
    [_updTask resume];
}

- (void)URLSession:(NSURLSession *)session
              task:(NSURLSessionTask *)task
willPerformHTTPRedirection:(NSHTTPURLResponse *)response
        newRequest:(NSURLRequest *)request
 completionHandler:(void (^)(NSURLRequest *))completionHandler {
    if (!amnURLTrusted(request.URL)) { completionHandler(nil); return; }
    completionHandler(request);
}

- (void)URLSession:(NSURLSession *)session
      downloadTask:(NSURLSessionDownloadTask *)downloadTask
      didWriteData:(int64_t)bytesWritten
 totalBytesWritten:(int64_t)totalBytesWritten
totalBytesExpectedToWrite:(int64_t)totalBytesExpectedToWrite {
    if (!_updBar) return;
    if (totalBytesExpectedToWrite > 0) {
        if (_updBar.indeterminate) {
            [_updBar stopAnimation:nil];
            _updBar.indeterminate = NO;
        }
        _updBar.doubleValue = 100.0 * (double)totalBytesWritten / (double)totalBytesExpectedToWrite;
        double mb = totalBytesWritten / (1024.0 * 1024.0);
        double tot = totalBytesExpectedToWrite / (1024.0 * 1024.0);
        NSString *ver = _updInfo[@"version"] ?: @"";
        _updLabel.stringValue = [NSString stringWithFormat:@"正在下载 %@…  %.1f / %.1f MB",
                                 ver, mb, tot];
    }
}

- (void)URLSession:(NSURLSession *)session
              task:(NSURLSessionTask *)task
didCompleteWithError:(NSError *)error {
    if (!error) return;
    if ([error.domain isEqualToString:NSURLErrorDomain] && error.code == NSURLErrorCancelled) {
        _updBusy = NO;
        return;
    }
    [self updateFail:error.localizedDescription ?: @"下载失败。"];
}

- (void)URLSession:(NSURLSession *)session
      downloadTask:(NSURLSessionDownloadTask *)downloadTask
didFinishDownloadingToURL:(NSURL *)location {
    NSHTTPURLResponse *http = ([downloadTask.response isKindOfClass:NSHTTPURLResponse.class]
                               ? (NSHTTPURLResponse *)downloadTask.response : nil);
    if (_updCancel) { _updBusy = NO; return; }
    if (http && http.statusCode != 200) {
        [self updateFail:[NSString stringWithFormat:@"下载失败（HTTP %ld）。", (long)http.statusCode]];
        return;
    }
    NSString *tmp = [NSTemporaryDirectory() stringByAppendingPathComponent:
                     [NSString stringWithFormat:@"amnote-upd-%@", NSUUID.UUID.UUIDString]];
    NSError *err = nil;
    NSFileManager *fm = NSFileManager.defaultManager;
    if (![fm createDirectoryAtPath:tmp withIntermediateDirectories:YES attributes:nil error:&err]) {
        [self updateFail:err.localizedDescription ?: @"建临时目录失败。"];
        return;
    }
    NSString *zipPath = [tmp stringByAppendingPathComponent:kUpdateAsset];
    NSURL *destURL = [NSURL fileURLWithPath:zipPath];
    if (![fm moveItemAtURL:location toURL:destURL error:&err]) {
        err = nil;
        if (![fm copyItemAtURL:location toURL:destURL error:&err]) {
            [self updateFail:err.localizedDescription ?: @"保存安装包失败。"];
            return;
        }
    }

    NSString *digest = _updInfo[@"digest"];
    NSNumber *size = _updInfo[@"size"];
    NSString *ver = _updInfo[@"version"];
    _updLabel.stringValue = @"正在校验…";
    _updBar.indeterminate = YES;
    [_updBar startAnimation:nil];

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *fail = [self verifyAndStageZip:zipPath inDir:tmp
                                          digest:digest size:size expectVer:ver];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (_updCancel) { _updBusy = NO; return; }
            if (fail) { [self updateFail:fail]; return; }
            [self installStagedApp:[tmp stringByAppendingPathComponent:@"AM·Note.app"]];
        });
    });
}

/// 后台线程：核 sha256、解压、核对 bundle id。成功后把 app 挪到 dir/AM·Note.app。
- (NSString *)verifyAndStageZip:(NSString *)zip inDir:(NSString *)dir
                         digest:(NSString *)digest size:(NSNumber *)size expectVer:(NSString *)ver {
    NSFileManager *fm = NSFileManager.defaultManager;
    if (size) {
        NSDictionary *attr = [fm attributesOfItemAtPath:zip error:nil];
        unsigned long long got = [attr[NSFileSize] unsignedLongLongValue];
        if (got != size.unsignedLongLongValue)
            return [NSString stringWithFormat:@"安装包大小不对（%llu，期望 %@）。", got, size];
    }
    if (digest.length) {
        NSString *want = digest;
        NSRange col = [want rangeOfString:@":"];
        if (col.location != NSNotFound) {
            NSString *alg = [want substringToIndex:col.location].lowercaseString;
            want = [want substringFromIndex:col.location + 1];
            if (![alg isEqualToString:@"sha256"])
                return [NSString stringWithFormat:@"不认识的校验算法：%@。", alg];
        }
        NSString *got = amnSHA256File(zip);
        if (!got.length) return @"算不了安装包的校验值。";
        if ([got caseInsensitiveCompare:want] != NSOrderedSame)
            return @"安装包校验失败，没有安装。请到 GitHub Releases 重新下载。";
    }

    NSString *outDir = [dir stringByAppendingPathComponent:@"out"];
    NSTask *t = [NSTask new];
    t.executableURL = [NSURL fileURLWithPath:@"/usr/bin/ditto"];
    t.arguments = @[ @"-xk", zip, outDir ];
    t.standardInput = NSFileHandle.fileHandleWithNullDevice;
    t.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    t.standardError = NSFileHandle.fileHandleWithNullDevice;
    NSError *e = nil;
    if (![t launchAndReturnError:&e]) return e.localizedDescription ?: @"解压失败。";
    [t waitUntilExit];
    if (t.terminationStatus != 0) return @"解压安装包失败。";

    NSString *app = amnFindApp(outDir);
    if (!app.length) return @"压缩包里没有 AM·Note.app。";

    NSString *plistPath = [app stringByAppendingPathComponent:@"Contents/Info.plist"];
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:plistPath];
    if (![info[@"CFBundleIdentifier"] isEqualToString:@"app.amnote"])
        return @"安装包的 Bundle ID 不是 app.amnote，已中止。";
    NSString *gotVer = info[@"CFBundleShortVersionString"] ?: @"";
    if (amnCmpVersion(gotVer, amnShortVersion()) <= 0)
        return [NSString stringWithFormat:@"包里的版本是 %@，不比现在的新。", gotVer];
    if (ver.length && amnCmpVersion(gotVer, ver) < 0)
        return [NSString stringWithFormat:@"包里的版本是 %@，比 GitHub 上的 %@ 还旧。", gotVer, ver];
    NSString *exeName = info[@"CFBundleExecutable"] ?: @"AM·Note";
    NSString *exe = [[app stringByAppendingPathComponent:@"Contents/MacOS"]
                     stringByAppendingPathComponent:exeName];
    if (![fm isExecutableFileAtPath:exe]) return @"安装包里缺少可执行文件。";

    NSString *staged = [dir stringByAppendingPathComponent:@"AM·Note.app"];
    if ([fm fileExistsAtPath:staged]) [fm removeItemAtPath:staged error:nil];
    if (![fm moveItemAtPath:app toPath:staged error:&e])
        return e.localizedDescription ?: @"挪新版本失败。";
    NSTask *xa = [NSTask new];
    xa.executableURL = [NSURL fileURLWithPath:@"/usr/bin/xattr"];
    xa.arguments = @[ @"-dr", @"com.apple.quarantine", staged ];
    xa.standardInput = NSFileHandle.fileHandleWithNullDevice;
    xa.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    xa.standardError = NSFileHandle.fileHandleWithNullDevice;
    [xa launchAndReturnError:NULL];
    [xa waitUntilExit];
    return nil;
}

- (void)installStagedApp:(NSString *)staged {
    if (_updCancel) { _updBusy = NO; return; }
    NSString *dest = NSBundle.mainBundle.bundlePath;
    if (![dest.pathExtension.lowercaseString isEqualToString:@"app"]) {
        [self updateFail:@"当前不是从 .app 运行的，没法自动替换。请到 GitHub 手动下载。"];
        return;
    }
    if (![[NSFileManager defaultManager] fileExistsAtPath:staged]) {
        [self updateFail:@"找不到解好的新版本。"];
        return;
    }

    BOOL dirty = _win.documentEdited || [self stateFlag:@"dirty"];
    if (!dirty) {
        for (NSWindow *w in _soloWins) if (w.documentEdited) { dirty = YES; break; }
    }
    if (dirty) {
        NSAlert *a = [NSAlert new];
        a.alertStyle = NSAlertStyleWarning;
        a.messageText = @"有改动还没保存";
        a.informativeText = @"安装更新要退出 AM·Note。未保存的改动会丢掉。";
        [a addButtonWithTitle:@"回去保存"];
        NSButton *go = [a addButtonWithTitle:@"放弃改动并更新"];
        if (@available(macOS 11.0, *)) go.hasDestructiveAction = YES;
        if (_win.isVisible) {
            [a beginSheetModalForWindow:_win completionHandler:^(NSModalResponse r) {
                if (r != NSAlertSecondButtonReturn) {
                    _updBusy = NO;
                    [self closeUpdateProgress];
                    return;
                }
                [self launchUpdateHelperFrom:staged to:dest];
            }];
            return;
        }
        if ([a runModal] != NSAlertSecondButtonReturn) {
            _updBusy = NO;
            [self closeUpdateProgress];
            return;
        }
    }
    [self launchUpdateHelperFrom:staged to:dest];
}

- (void)launchUpdateHelperFrom:(NSString *)src to:(NSString *)dst {
    NSString *script = [NSTemporaryDirectory() stringByAppendingPathComponent:@"amnote-upd-install.sh"];
    NSString *body =
        @"#!/bin/bash\n"
        @"trap '' HUP\n"
        @"PID=\"$1\"; SRC=\"$2\"; DST=\"$3\"\n"
        @"LOG=\"${TMPDIR:-/tmp}/amnote-update.log\"\n"
        @"log() { echo \"$(date '+%Y-%m-%d %H:%M:%S') $*\" >> \"$LOG\"; }\n"
        @"log \"wait pid=$PID\"\n"
        @"n=0\n"
        @"while kill -0 \"$PID\" 2>/dev/null; do\n"
        @"  n=$((n+1))\n"
        @"  if [ \"$n\" -gt 300 ]; then log timeout; exit 1; fi\n"
        @"  sleep 0.2\n"
        @"done\n"
        @"sleep 0.4\n"
        @"/usr/bin/xattr -dr com.apple.quarantine \"$SRC\" >/dev/null 2>&1 || true\n"
        @"OLD=\"${DST}.amn-old-$$\"\n"
        @"ok=0\n"
        @"if mv \"$DST\" \"$OLD\" 2>/dev/null; then\n"
        @"  if /usr/bin/ditto \"$SRC\" \"$DST\"; then rm -rf \"$OLD\"; ok=1\n"
        @"  else rm -rf \"$DST\"; mv \"$OLD\" \"$DST\" 2>/dev/null; log ditto-restore\n"
        @"  fi\n"
        @"fi\n"
        @"if [ \"$ok\" -eq 0 ]; then\n"
        @"  if /usr/bin/ditto \"$SRC\" \"$DST\"; then ok=1\n"
        @"  else\n"
        @"    /usr/bin/osascript - \"$SRC\" \"$DST\" <<'AS' >>\"$LOG\" 2>&1\n"
        @"on run argv\n"
        @"  set src to item 1 of argv\n"
        @"  set dst to item 2 of argv\n"
        @"  do shell script \"/usr/bin/ditto \" & quoted form of src & \" \" & quoted form of dst with administrator privileges\n"
        @"end run\n"
        @"AS\n"
        @"    if [ $? -eq 0 ]; then ok=1; fi\n"
        @"  fi\n"
        @"fi\n"
        @"if [ \"$ok\" -eq 0 ]; then log fail; exit 1; fi\n"
        @"/usr/bin/xattr -dr com.apple.quarantine \"$DST\" >/dev/null 2>&1 || true\n"
        @"log open\n"
        @"/usr/bin/open \"$DST\"\n"
        @"PARENT=\"$(/usr/bin/dirname \"$SRC\")\"\n"
        @"case \"$PARENT\" in */amnote-upd-*) rm -rf \"$PARENT\" ;; esac\n"
        @"exit 0\n";
    NSError *err = nil;
    if (![body writeToFile:script atomically:YES encoding:NSUTF8StringEncoding error:&err]) {
        [self updateFail:err.localizedDescription ?: @"写更新脚本失败。"];
        return;
    }
    [[NSFileManager defaultManager] setAttributes:@{ NSFilePosixPermissions: @0755 }
                                     ofItemAtPath:script error:nil];

    NSTask *t = [NSTask new];
    t.executableURL = [NSURL fileURLWithPath:@"/usr/bin/nohup"];
    t.arguments = @[ @"/bin/bash", script,
                     [NSString stringWithFormat:@"%d", getpid()], src, dst ];
    t.standardInput = NSFileHandle.fileHandleWithNullDevice;
    t.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    t.standardError = NSFileHandle.fileHandleWithNullDevice;
    if (![t launchAndReturnError:&err]) {
        [self updateFail:err.localizedDescription ?: @"拉不起更新脚本。"];
        return;
    }

    _installingUpdate = YES;
    [self closeUpdateProgress];
    [NSApp terminate:nil];
}

// MARK: WKNavigationDelegate

static BOOL isLocalURL(NSURL *u) {
    NSString *h = u.host;
    if ([h isEqualToString:@"127.0.0.1"] || [h isEqualToString:@"localhost"]) return YES;
    NSString *s = u.scheme;
    return [s isEqualToString:@"about"] || [s isEqualToString:@"blob"] || [s isEqualToString:@"data"];
}

/// 主框只许停在门户自己的地址上。库里的 .html 一旦顶栏载入，就变成
/// 「页面里的页面」，壳的快捷键和 AMN 通道都会对不上。
static BOOL isShellMainURL(NSURL *u) {
    if (!u) return NO;
    NSString *s = u.scheme;
    if ([s isEqualToString:@"about"] || [s isEqualToString:@"blob"] || [s isEqualToString:@"data"]) return YES;
    NSString *path = u.path ?: @"";
    if ([path isEqualToString:@"/portal"] || [path isEqualToString:@"/portal/"]) return YES;
    if ([path hasPrefix:@"/__"]) return YES;
    return NO;
}

- (void)webView:(WKWebView *)w
    decidePolicyForNavigationAction:(WKNavigationAction *)act
                    decisionHandler:(void (^)(WKNavigationActionPolicy))done {
    NSURL *u = act.request.URL;
    if (!u) { done(WKNavigationActionPolicyAllow); return; }
    if (!isLocalURL(u)) {
        // 库外的链接不在门户里开，交给系统默认浏览器
        [NSWorkspace.sharedWorkspace openURL:u];
        done(WKNavigationActionPolicyCancel);
        return;
    }
    // iframe 里的库文件（HTML 阅读器）必须放行；顶栏导航到同一份则拦掉。
    BOOL mainFrame = !act.targetFrame || act.targetFrame.isMainFrame;
    if (!mainFrame) { done(WKNavigationActionPolicyAllow); return; }
    if (isShellMainURL(u)) { done(WKNavigationActionPolicyAllow); return; }
    done(WKNavigationActionPolicyCancel);
}

- (void)webView:(WKWebView *)w didFinishNavigation:(WKNavigation *)nav {
    if (w != _web) return;          // 辅窗 / 独立窗的完成不能冒充主门户就绪
    _loadedOnce = YES;
    [self showWindowIfNeeded];      // N-8：内容就绪了才把窗口放出来
    [self refreshState];
    [self flushPending];
}

/// 上面 decidePolicy 里 Cancel 掉外链会在这里报一个 102（策略中断），那是正常的，不能当错误。
static BOOL isBenignNavError(NSError *e) {
    if ([e.domain isEqualToString:NSURLErrorDomain] && e.code == NSURLErrorCancelled) return YES;
    if ([e.domain isEqualToString:@"WebKitErrorDomain"] && e.code == 102) return YES;
    return NO;
}

- (void)webView:(WKWebView *)w didFailNavigation:(WKNavigation *)nav withError:(NSError *)e {
    if (w != _web || isBenignNavError(e) || _loadedOnce) return;
    [self failReason:@"门户加载失败" detail:e.localizedDescription];
}

- (void)webView:(WKWebView *)w didFailProvisionalNavigation:(WKNavigation *)nav withError:(NSError *)e {
    if (w != _web || isBenignNavError(e) || _loadedOnce) return;
    [self failReason:@"连不上门户服务"
              detail:[NSString stringWithFormat:@"%@\n\n端口 %ld\n\n%@",
                      e.localizedDescription, (long)_svc.port, [_svc tailStderr]]];
}

// MARK: WKUIDelegate
//
// target="_blank" 和 window.open 交给系统浏览器。门户「在浏览器里打开」按钮、
// MD 正文里的外链，走的都是这条。不实现的话它们全是死链。
//
// **例外是自家门户**：`?solo=1` 是独立阅读窗（一份正文、没有浏览器顶栏）；
// `?win=1` 是完整浏览器辅窗（跟主窗同一套网页顶栏）。目的地都是 AM·Note 自己，
// 甩给 Safari 等于换了个 app。差一条就当外链，交回浏览器。

- (WKWebView *)webView:(WKWebView *)w
    createWebViewWithConfiguration:(WKWebViewConfiguration *)cfg
               forNavigationAction:(WKNavigationAction *)act
                    windowFeatures:(WKWindowFeatures *)feat {
    NSURL *u = act.request.URL;
    if ([self isSoloURL:u]) return [self makeSoloWebWithConfiguration:cfg features:feat];
    if ([self isAuxURL:u])  return [self makeBrowserWebWithConfiguration:cfg features:feat];
    if (u) [NSWorkspace.sharedWorkspace openURL:u];
    return nil;
}

/// 网页里的 window.close()。独立文稿窗把自己那一份挪进废纸篓之后就靠这一句收尾——
/// 不接的话窗口原地留着，显示的还是一份已经不在库里的笔记。主窗口不给关：
/// 门户一卸整个界面就没了，那条路只能走红灯／⌘W。
- (void)webViewDidClose:(WKWebView *)web {
    NSWindow *win = web.window;
    if (win && win != _win) [win performClose:nil];
}

/// 自家这次起的那个门户服务：本机 http、端口对得上。solo / aux 都先过这一关。
- (BOOL)isOurPortalURL:(NSURL *)u {
    if (!u || _svc.port <= 0) return NO;
    if (![u.scheme isEqualToString:@"http"]) return NO;
    if (!([u.host isEqualToString:@"127.0.0.1"] || [u.host isEqualToString:@"localhost"])) return NO;
    if (u.port.integerValue != _svc.port) return NO;
    return YES;
}

/// 是不是「自家门户的独立文稿窗口」。查询串里有 solo=1。
- (BOOL)isSoloURL:(NSURL *)u {
    if (![self isOurPortalURL:u]) return NO;
    NSURLComponents *c = [NSURLComponents componentsWithURL:u resolvingAgainstBaseURL:NO];
    for (NSURLQueryItem *q in c.queryItems)
        if ([q.name isEqualToString:@"solo"] && [q.value isEqualToString:@"1"]) return YES;
    return NO;
}

/// 完整浏览器辅窗。路径必须是 /portal，查询串 win=1——不能跟 solo 混，
/// 否则一份正文会被开成带标签栏的空浏览器。
- (BOOL)isAuxURL:(NSURL *)u {
    if (![self isOurPortalURL:u]) return NO;
    NSString *path = u.path ?: @"";
    if (!([path isEqualToString:@"/portal"] || [path isEqualToString:@"/portal/"])) return NO;
    NSURLComponents *c = [NSURLComponents componentsWithURL:u resolvingAgainstBaseURL:NO];
    for (NSURLQueryItem *q in c.queryItems)
        if ([q.name isEqualToString:@"win"] && [q.value isEqualToString:@"1"]) return YES;
    return NO;
}

/// 建一个独立文稿窗口。**这块 webview 必须用传进来的那份 configuration 建**——
/// WebKit 的硬要求，换一份会崩。返回之后也**不要自己 loadRequest**，
/// 框架会把 act.request 载进去，自己再载一次等于载两遍。
- (WKWebView *)makeSoloWebWithConfiguration:(WKWebViewConfiguration *)cfg
                                   features:(WKWindowFeatures *)feat {
    CGFloat ww = feat.width  ? feat.width.doubleValue  : kSoloW;
    CGFloat hh = feat.height ? feat.height.doubleValue : kSoloH;
    ww = MAX(kSoloMinW, MIN(ww, 2400));
    hh = MAX(kSoloMinH, MIN(hh, 1800));

    NSWindow *win = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, ww, hh)
                                                styleMask:(NSWindowStyleMaskTitled |
                                                           NSWindowStyleMaskClosable |
                                                           NSWindowStyleMaskMiniaturizable |
                                                           NSWindowStyleMaskResizable)
                                                  backing:NSBackingStoreBuffered
                                                    defer:NO];
    win.title = @"AM·Note";
    win.identifier = @"amn.solo";          // ⌘W / Esc 靠这个跟浏览器辅窗分开
    win.minSize = NSMakeSize(kSoloMinW, kSoloMinH);
    win.releasedWhenClosed = NO;          // 关掉的清理走 windowWillClose:
    win.delegate = self;
    win.tabbingMode = NSWindowTabbingModeDisallowed;
    NSView *content = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, ww, hh)];
    content.wantsLayer = YES;
    win.contentView = content;
    // 从主窗口左上角往下错开，别几个窗口叠在同一个位置
    NSPoint from = _win ? NSMakePoint(NSMinX(_win.frame) + 34, NSMaxY(_win.frame) - 34)
                        : NSZeroPoint;
    if (NSEqualPoints(from, NSZeroPoint)) [win center]; else [win cascadeTopLeftFromPoint:from];

    // amn 通道多半跟着 opener 的 controller 一起带过来了；万一没有就补一次。
    // 同名重复注册会抛 NSInvalidArgumentException，所以包一层——抛了正说明已经有了。
    @try { [cfg.userContentController addScriptMessageHandler:self name:@"amn"]; }
    @catch (NSException *e) { }

    PortalWebView *web = [[PortalWebView alloc] initWithFrame:content.bounds configuration:cfg];
    web.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    web.navigationDelegate = self;
    web.UIDelegate = self;
    web.allowsBackForwardNavigationGestures = NO;
    if (@available(macOS 13.3, *)) { web.inspectable = YES; }
    __weak NSWindow *weakWin = win;
    // 独立窗口里 Esc 没有别的语义（没有列表可退回、没有面板可关），就是关掉它
    web.onEscape = ^{ [weakWin performClose:nil]; };

    [content addSubview:web];
    [_soloWebs addObject:web];
    [_soloWins addObject:win];
    [win makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    return web;
}

/// 再建一个完整浏览器窗口。跟主窗同一套样式（透明标题栏、标题隐藏、没有原生工具栏），
/// 网页自己画标签和地址栏。configuration 必须用 WebKit 传进来的那份，也不能自己 loadRequest。
- (WKWebView *)makeBrowserWebWithConfiguration:(WKWebViewConfiguration *)cfg
                                      features:(WKWindowFeatures *)feat {
    CGFloat ww = feat.width  ? feat.width.doubleValue  : kWinW;
    CGFloat hh = feat.height ? feat.height.doubleValue : kWinH;
    ww = MAX(kMinW, MIN(ww, 2400));
    hh = MAX(kMinH, MIN(hh, 1800));

    NSWindow *win = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, ww, hh)
                                                styleMask:(NSWindowStyleMaskTitled |
                                                           NSWindowStyleMaskFullSizeContentView |
                                                           NSWindowStyleMaskClosable |
                                                           NSWindowStyleMaskMiniaturizable |
                                                           NSWindowStyleMaskResizable)
                                                  backing:NSBackingStoreBuffered
                                                    defer:NO];
    win.title = @"AM·Note";
    win.identifier = @"amn.browser";
    win.titleVisibility = NSWindowTitleHidden;
    win.titlebarAppearsTransparent = YES;
    win.titlebarSeparatorStyle = NSTitlebarSeparatorStyleNone;
    win.movableByWindowBackground = YES;
    win.minSize = NSMakeSize(kMinW, kMinH);
    win.releasedWhenClosed = NO;
    win.delegate = self;
    win.tabbingMode = NSWindowTabbingModeDisallowed;
    NSView *content = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, ww, hh)];
    content.wantsLayer = YES;
    win.contentView = content;
    applySeamlessChrome(win);

    NSPoint from = _win ? NSMakePoint(NSMinX(_win.frame) + 34, NSMaxY(_win.frame) - 34)
                        : NSZeroPoint;
    if (NSEqualPoints(from, NSZeroPoint)) [win center]; else [win cascadeTopLeftFromPoint:from];

    @try { [cfg.userContentController addScriptMessageHandler:self name:@"amn"]; }
    @catch (NSException *e) { }

    PortalWebView *web = [[PortalWebView alloc] initWithFrame:content.bounds configuration:cfg];
    web.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    web.navigationDelegate = self;
    web.UIDelegate = self;
    web.allowsBackForwardNavigationGestures = NO;
    if (@available(macOS 13.3, *)) { web.inspectable = YES; }
    __weak typeof(self) weakSelf = self;
    // 跟主窗一样：Esc 交给网页自己的浮层 / 编辑链，不能把整窗关掉。
    web.onEscape = ^{ [weakSelf amn:@"escape" args:nil]; };

    [content addSubview:web];
    [_soloWebs addObject:web];
    [_soloWins addObject:win];
    [win makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    return web;
}

/// 独立窗和浏览器辅窗发来的消息。ready 故意不走——那条会把主窗口排的「打开这份」
/// 冲给发件页，辅窗收了就等于把主窗口的收件吃掉。theme / doc / dirty 仍按发件窗更新。
- (void)soloMessage:(NSDictionary *)m from:(WKWebView *)web {
    NSString *type = [m[@"type"] isKindOfClass:NSString.class] ? m[@"type"] : nil;
    NSWindow *win = web.window;
    if (!type.length || !win) return;
    if ([type isEqualToString:@"theme"]) {
        NSString *theme = [m[@"value"] isKindOfClass:NSString.class] ? m[@"value"] : @"auto";
        applyThemeToWindow(win, theme);
        applySeamlessChrome(win);
        return;
    }
    if ([type isEqualToString:@"doc"]) {
        NSString *title = [m[@"title"] isKindOfClass:NSString.class] ? m[@"title"] : nil;
        NSString *path  = [m[@"path"]  isKindOfClass:NSString.class] ? m[@"path"]  : nil;
        win.title = title.length ? title : @"AM·Note";
        win.representedURL = path.length ? [NSURL fileURLWithPath:path] : nil;   // 代理图标照样能拖
        return;
    }
    if ([type isEqualToString:@"dirty"]) { win.documentEdited = [m[@"on"] boolValue]; return; }
    if ([type isEqualToString:@"clip"]) {
        NSString *t = [m[@"text"] isKindOfClass:NSString.class] ? m[@"text"] : nil;
        [self copyToPasteboard:t];
        return;
    }
    if ([type isEqualToString:@"share"]) {
        NSString *p = [m[@"path"] isKindOfClass:NSString.class] ? m[@"path"] : nil;
        [self sharePath:p];
        return;
    }
}

/// 弹框贴在发消息的那个窗口上。独立文稿窗口里的确认框跑去主窗口，
/// 会变成「按了没反应、另一个窗口在等你」。
- (NSWindow *)hostWindowFor:(WKWebView *)w { return w.window ?: _win; }

- (void)webView:(WKWebView *)w
    runJavaScriptAlertPanelWithMessage:(NSString *)msg
                      initiatedByFrame:(WKFrameInfo *)frame
                     completionHandler:(void (^)(void))done {
    NSAlert *a = [NSAlert new];
    a.messageText = @"AM·Note";
    a.informativeText = msg;
    [a addButtonWithTitle:@"好"];
    [a beginSheetModalForWindow:[self hostWindowFor:w] completionHandler:^(NSModalResponse r) { done(); }];
}

/// 门户的确认框走这里。两种形态：
///
/// 一、结构化（门户现行做法）。同步 confirm() 的字符串里塞了五段，分隔符是 U+0001
///     （正文里不可能出现的控制字符）：
///
///         AMN2 \x01 标题 \x01 正文 \x01 确认按钮|取消按钮 \x01 destructive
///
///     第 1 段固定 AMN2，用来认这个协议；第 4 段两颗按钮用半角竖线分开，第一颗是确认
///     （返回 YES）；第 5 段是 "0" 表示确认那颗要标红，空串表示不标。正文可以是空串。
///
/// 二、认不出 AMN2 前缀的，按老样子弹「AM·Note ／ 好 ／ 取消」。老门户、iframe 里的
///     页面、以及门户没来得及改的调用点还靠这条，删不得。
- (void)webView:(WKWebView *)w
    runJavaScriptConfirmPanelWithMessage:(NSString *)msg
                        initiatedByFrame:(WKFrameInfo *)frame
                       completionHandler:(void (^)(BOOL))done {
    NSString *sep = [NSString stringWithFormat:@"%C", (unichar)0x01];
    if ([msg hasPrefix:[@"AMN2" stringByAppendingString:sep]]) {
        NSArray<NSString *> *p = [msg componentsSeparatedByString:sep];
        NSString *title = p.count > 1 ? p[1] : @"";
        NSString *body  = p.count > 2 ? p[2] : @"";
        NSArray<NSString *> *btns = [(p.count > 3 ? p[3] : @"")
                                     componentsSeparatedByString:@"|"];
        BOOL destructive = (p.count > 4) && [p[4] isEqualToString:@"0"];

        NSAlert *sa = [NSAlert new];
        sa.messageText = title.length ? title : @"AM·Note";
        sa.informativeText = body;
        NSString *okTitle = (btns.count > 0 && [btns[0] length]) ? btns[0] : @"好";
        NSButton *ok = [sa addButtonWithTitle:okTitle];
        NSButton *cancel = (btns.count > 1 && [btns[1] length])
                         ? [sa addButtonWithTitle:btns[1]] : nil;
        cancel.keyEquivalent = @"\033";          // Esc。与 @"\e" 同一个字符，写八进制免得吃编译器扩展
        if (destructive) {
            if ([ok respondsToSelector:@selector(setHasDestructiveAction:)]) {
                ok.hasDestructiveAction = YES;   // macOS 11+：标红，并把默认按钮让给另一颗
            }
            // 再显式把回车从「确认」上摘掉。破坏性的场合，手滑一下回车不该把东西删了；
            // 上面那条没生效时最坏也只是回车不响应，Esc 仍然能取消。
            ok.keyEquivalent = @"";
        }
        [sa beginSheetModalForWindow:[self hostWindowFor:w] completionHandler:^(NSModalResponse r) {
            done(r == NSAlertFirstButtonReturn);   // 第一颗＝确认
        }];
        return;
    }

    NSAlert *a = [NSAlert new];
    a.messageText = @"AM·Note";
    a.informativeText = msg;
    [a addButtonWithTitle:@"好"];
    [a addButtonWithTitle:@"取消"];
    [a beginSheetModalForWindow:[self hostWindowFor:w] completionHandler:^(NSModalResponse r) {
        done(r == NSAlertFirstButtonReturn);
    }];
}

- (void)webView:(WKWebView *)w
    runJavaScriptTextInputPanelWithPrompt:(NSString *)prompt
                              defaultText:(NSString *)def
                         initiatedByFrame:(WKFrameInfo *)frame
                        completionHandler:(void (^)(NSString *))done {
    NSAlert *a = [NSAlert new];
    a.messageText = @"AM·Note";
    a.informativeText = prompt;
    NSTextField *f = [[NSTextField alloc] initWithFrame:NSMakeRect(0, 0, 300, 24)];
    f.stringValue = def ?: @"";
    a.accessoryView = f;
    [a addButtonWithTitle:@"好"];
    [a addButtonWithTitle:@"取消"];
    [a beginSheetModalForWindow:[self hostWindowFor:w] completionHandler:^(NSModalResponse r) {
        done(r == NSAlertFirstButtonReturn ? f.stringValue : nil);
    }];
}

// MARK: 退出

/// NO：关窗口不退 app。服务、状态栏图标、Dock 徽标都留着，
/// Agent 的 amnote_open 才有人接（设计约束 4）。全退只有两条路：⌘Q、状态栏菜单里的「退出」。
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)s { return NO; }

/// 门户的 beforeunload 在 WKWebView 里是被忽略的，所以未保存改动这道拦必须在原生这边做。
/// 新门户看 AMN.state().dirty，老门户退回 TABS.some(dirty)。
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (_installingUpdate) return NSTerminateNow;
    BOOL soloDirty = NO;
    for (NSWindow *w in _soloWins) if (w.documentEdited) { soloDirty = YES; break; }
    if (soloDirty) {
        NSAlert *a = [NSAlert new];
        a.alertStyle = NSAlertStyleWarning;
        a.messageText = @"有改动还没保存";
        a.informativeText = @"独立窗口里正在编辑的稿子有未保存的改动，现在退出会丢掉。";
        [a addButtonWithTitle:@"回去保存"];
        NSButton *quit = [a addButtonWithTitle:@"直接退出"];
        if (@available(macOS 11.0, *)) { quit.hasDestructiveAction = YES; }
        if ([a runModal] != NSAlertSecondButtonReturn) return NSTerminateCancel;
    }
    if (!_web || !_loadedOnce) return NSTerminateNow;
    [_web evaluateJavaScript:
        @"(function(){"
         "try{if(window.AMN&&typeof AMN.state==='function'){var s=AMN.state();"
         "if(typeof s==='string')s=JSON.parse(s);if(s&&typeof s.dirty!=='undefined')return s.dirty?1:0}}catch(e){}"
         "try{return (typeof TABS!=='undefined'&&typeof dirty==='function'&&TABS.some(dirty))?1:0}catch(e){}"
         "return 0})()"
           completionHandler:^(id r, NSError *e) {
        BOOL dirty = [r respondsToSelector:@selector(integerValue)] && [r integerValue] == 1;
        if (!dirty) { [NSApp replyToApplicationShouldTerminate:YES]; return; }
        NSAlert *a = [NSAlert new];
        a.alertStyle = NSAlertStyleWarning;
        a.messageText = @"有改动还没保存";
        a.informativeText = @"门户里正在编辑的稿子有未保存的改动，现在退出会丢掉。";
        [a addButtonWithTitle:@"回去保存"];
        NSButton *quit = [a addButtonWithTitle:@"直接退出"];
        if (@available(macOS 11.0, *)) { quit.hasDestructiveAction = YES; }
        [NSApp replyToApplicationShouldTerminate:([a runModal] == NSAlertSecondButtonReturn)];
    }];
    return NSTerminateLater;
}

/// ⌘Q / 状态栏「退出」的终点。这条没变：全退就是把自己起的服务一起收掉。
- (void)applicationWillTerminate:(NSNotification *)n {
    _quitting = YES;
    [_stateTimer invalidate];
    if (!_installingUpdate) {
        [_updTask cancel];
        _updTask = nil;
        [_updSession invalidateAndCancel];
        _updSession = nil;
    }
    if (_statusItem) { [NSStatusBar.systemStatusBar removeStatusItem:_statusItem]; _statusItem = nil; }
    [_svc stop];
}

@end

// ─────────────────────────── 入口 ───────────────────────────

/// NSApplication.delegate 是 weak 的，delegate 必须有强引用挂在别处，不然一出
/// main 的 autoreleasepool 就被回收，表现是窗口开出来立刻崩。
static AppDelegate *gDelegate = nil;

int main(void) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        gDelegate = [AppDelegate new];
        app.delegate = gDelegate;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        [app run];
    }
    return 0;
}
