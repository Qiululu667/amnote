// Probe: what does WKWebView's paste event actually see vs NSPasteboard?
// Compile: clang -fobjc-arc -framework AppKit -framework WebKit paste_clip_probe.m -o paste_clip_probe
#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>

@interface Probe : NSObject <WKScriptMessageHandler, WKNavigationDelegate>
@property (nonatomic) WKWebView *web;
@property (nonatomic) BOOL loaded;
@property (nonatomic) NSString *result;
@end

@implementation Probe
- (void)userContentController:(WKUserContentController *)ucc
      didReceiveScriptMessage:(WKScriptMessage *)message {
    if ([message.body isKindOfClass:NSString.class]) self.result = message.body;
}
- (void)webView:(WKWebView *)w didFinishNavigation:(WKNavigation *)nav { self.loaded = YES; }
@end

static NSString *head(NSString *s, NSUInteger n) {
    if (!s.length) return @"";
    if (s.length <= n) return s;
    return [[s substringToIndex:n] stringByAppendingString:@"…"];
}

int main(int argc, char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSPasteboard *pb = NSPasteboard.generalPasteboard;
        NSString *caseName = argc > 1 ? [NSString stringWithUTF8String:argv[1]] : @"mixed";
        [pb clearContents];
        NSString *html = nil, *text = @"1、\n这段字夹在两张图中间\n2、结尾";
        BOOL addPng = NO;
        if ([caseName isEqualToString:@"html-only"]) {
            html = @"<html><body><!--StartFragment-->"
                @"<div>1、</div>"
                @"<img src=\"https://p3-lark-file.feishucdn.com/demo.png\" width=\"800\" height=\"400\">"
                @"<div>这段字夹在两张图中间</div>"
                @"<img src=\"https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/asynccode/?code=TOKEN\">"
                @"<div>2、结尾</div>"
                @"<!--EndFragment--></body></html>";
        } else if ([caseName isEqualToString:@"blob"]) {
            html = @"<html><body><div>1、</div>"
                @"<img src=\"blob:https://www.feishu.cn/1234-5678\">"
                @"<div>字</div></body></html>";
        } else if ([caseName isEqualToString:@"png-only"]) {
            text = @"";
            addPng = YES;
        } else {
            html = @"<html><body><!--StartFragment-->"
                @"<div>1、</div>"
                @"<img src=\"https://p3-lark-file.feishucdn.com/demo.png\" width=\"800\" height=\"400\">"
                @"<div>这段字夹在两张图中间</div>"
                @"<img src=\"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==\">"
                @"<div>2、结尾</div>"
                @"<!--EndFragment--></body></html>";
            addPng = YES;
        }
        if (html) [pb setString:html forType:NSPasteboardTypeHTML];
        if (text.length) [pb setString:text forType:NSPasteboardTypeString];
        NSData *png = [[NSData alloc] initWithBase64EncodedString:
            @"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            options:0];
        if (addPng) [pb setData:png forType:NSPasteboardTypePNG];
        printf("CASE %s\n", caseName.UTF8String);

        printf("BEFORE types:\n");
        for (NSString *t in pb.types) printf("  %s\n", t.UTF8String);
        NSString *h0 = [pb stringForType:NSPasteboardTypeHTML] ?: @"";
        printf("BEFORE html len %lu img_tags %d png %lu text %lu\n",
               (unsigned long)h0.length,
               (int)[[h0 lowercaseString] componentsSeparatedByString:@"<img"].count - 1,
               (unsigned long)[pb dataForType:NSPasteboardTypePNG].length,
               (unsigned long)[pb stringForType:NSPasteboardTypeString].length);

        Probe *p = [Probe new];
        WKUserContentController *ucc = [WKUserContentController new];
        [ucc addScriptMessageHandler:p name:@"probe"];
        WKWebViewConfiguration *cfg = [WKWebViewConfiguration new];
        cfg.userContentController = ucc;
        NSWindow *win = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,640,400)
                                                    styleMask:NSWindowStyleMaskTitled
                                                      backing:NSBackingStoreBuffered defer:NO];
        WKWebView *web = [[WKWebView alloc] initWithFrame:NSMakeRect(0,0,640,400) configuration:cfg];
        web.navigationDelegate = p;
        win.contentView = web;
        [win orderFront:nil];
        p.web = web;
        NSString *page = @"<!doctype html><meta charset=utf-8>"
            @"<div id=ed contenteditable=true style='min-height:200px'>edit</div>"
            @"<script>"
            @"const ed=document.getElementById('ed'); ed.focus();"
            @"document.execCommand('selectAll');"
            @"ed.addEventListener('paste', e=>{"
            @"  const dt=e.clipboardData;"
            @"  const html=dt?dt.getData('text/html'):'';"
            @"  const txt=dt?dt.getData('text/plain'):'';"
            @"  const types=dt? [...dt.types]:[];"
            @"  const files=dt? [...dt.files].map(f=>f.type+':'+f.size):[];"
            @"  const items=dt&&dt.items?[...dt.items].map(i=>i.kind+':'+i.type):[];"
            @"  e.preventDefault();"
            @"  window.webkit.messageHandlers.probe.postMessage(JSON.stringify({"
            @"    htmlLen:html.length, txtLen:txt.length, types, files, items,"
            @"    imgTags:(html.toLowerCase().split('<img').length-1),"
            @"    srcs:[...html.matchAll(/src=(\"[^\"]*\"|'[^']*')/gi)].map(m=>m[1].slice(1,-1)).slice(0,8),"
            @"    htmlHead:html.slice(0,500), txt:txt.slice(0,200)"
            @"  }));"
            @"});"
            @"</script>";
        [web loadHTMLString:page baseURL:[NSURL URLWithString:@"http://127.0.0.1/"]];
        NSDate *dead = [NSDate dateWithTimeIntervalSinceNow:5];
        while (!p.loaded && [dead timeIntervalSinceNow] > 0)
            [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:dead];
        if (!p.loaded) { fprintf(stderr, "page not loaded\n"); return 2; }

        [win makeKeyAndOrderFront:nil];
        [win makeFirstResponder:web];
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Warc-performSelector-leaks"
        [web performSelector:NSSelectorFromString(@"paste:") withObject:nil];
#pragma clang diagnostic pop

        dead = [NSDate dateWithTimeIntervalSinceNow:3];
        while (!p.result && [dead timeIntervalSinceNow] > 0)
            [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:dead];

        printf("JS_PASTE %s\n", p.result ? p.result.UTF8String : "(no event)");

        NSString *h1 = [pb stringForType:NSPasteboardTypeHTML] ?: @"";
        printf("AFTER html len %lu img_tags %d png %lu\n",
               (unsigned long)h1.length,
               (int)[[h1 lowercaseString] componentsSeparatedByString:@"<img"].count - 1,
               (unsigned long)[pb dataForType:NSPasteboardTypePNG].length);
        printf("AFTER html head: %s\n", head(h1, 400).UTF8String);
        [ucc removeScriptMessageHandlerForName:@"probe"];
    }
    return 0;
}
