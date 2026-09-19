#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书那种图文粘贴：HTTPS 下载、HTML 拆段、本地 HTTP 图落到 _图/。

    python3 scripts/paste_clip_check.py

不碰用户的库。上一版只测了 http://127.0.0.1，所以飞书 HTTPS 图全绿、真机全挂。
"""
import html as htmlmod
import http.server
import os
import re
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def html_clipboard_pieces(html):
    """跟 template.html htmlClipboardPieces 同一套：只要正文和 <img> 地址。"""
    if not html:
        return []
    from html.parser import HTMLParser

    skip = {"script", "style", "meta", "head", "link", "noscript"}
    block = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
             "blockquote", "pre", "section", "article", "figure",
             "figcaption", "dt", "dd", "table", "ul", "ol"}
    pieces = []
    buf = []

    def flush():
        t = "".join(buf)
        buf.clear()
        t = t.replace("\r\n", "\n")
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        if t.strip():
            pieces.append({"kind": "text", "text": t})

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "head", "noscript"):
                self._skip += 1
                return
            if tag in skip:
                return
            if self._skip:
                return
            ad = dict(attrs)
            if tag == "img":
                w = int(ad.get("width") or 0 or 0) if str(ad.get("width") or "").isdigit() else 0
                h = int(ad.get("height") or 0 or 0) if str(ad.get("height") or "").isdigit() else 0
                hint = " ".join([ad.get("class", ""), ad.get("alt", ""), ad.get("src", "")])
                if (w and h and w <= 32 and h <= 32) or re.search(
                        r"\bemoji\b|\bsticker\b|\bemoticon\b|\bemotion\b", hint, re.I):
                    return
                src = (ad.get("src") or ad.get("data-lark-image-uri")
                       or (("native-resource://sdk/image?key="+ad["data-image-key"])
                           if ad.get("data-image-key") else "")
                       or ad.get("data-src") or ad.get("data-origin-src")
                       or ad.get("data-original") or ad.get("origin-src")
                       or ad.get("data-image-src") or ad.get("data-origin")
                       or ad.get("data-origin-file") or "").strip()
                flush()
                pieces.append({"kind": "img", "src": src, "alt": ad.get("alt") or ""})
                return
            if tag == "br":
                buf.append("\n")
                return
            if tag in block and buf and not "".join(buf).endswith("\n"):
                buf.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "head", "noscript") and self._skip:
                self._skip -= 1
                return
            if self._skip:
                return
            if tag in block:
                buf.append("\n")

        def handle_data(self, data):
            if not self._skip:
                buf.append(data)

    P().feed(html)
    flush()
    return pieces


def fail(msg):
    raise SystemExit(msg)


def main():
    tmp = tempfile.mkdtemp(prefix="amnote-paste-")
    vault = os.path.join(tmp, "vault")
    support = os.path.join(tmp, "support")
    os.makedirs(vault)
    os.makedirs(support)
    os.environ["AMNOTE_SUPPORT_DIR"] = support
    os.environ["AMNOTE_HOME"] = tmp
    sys.path.insert(0, SRC)
    import fulltext
    import portal_server as ps
    fulltext.configure(vault)
    note = os.path.join(vault, "a.md")
    with open(note, "w", encoding="utf-8") as f:
        f.write("# 飞书粘贴\n")

    # 1) 本机 python.org urllib 对 HTTPS 过不了证书——这就是 5.13.1/5.13.2
    #    「测试全绿、飞书没图」的原因。这条只记现象，不作为硬失败。
    urllib_https = "unknown"
    try:
        urllib.request.urlopen("https://www.feishu.cn/favicon.ico", timeout=8)
        urllib_https = "ok"
    except Exception as e:
        urllib_https = type(e).__name__
    print("urllib https:", urllib_https)

    # 2) 修好之后：HTTPS 要能从钥匙串那条路拉到字节。favicon 是 ico，
    #    sniff 会拒，但错误必须是「只收 png…」而不是「拉不下来」。
    blob, err = ps._fetch_img_url("https://www.feishu.cn/favicon.ico")
    if blob:
        fail("favicon.ico should not sniff as png/jpg/gif/webp")
    if not err or "拉不下来" in err:
        fail("https fetch still broken (expected sniff reject): %r" % err)
    if "只收" not in err and "png" not in err.lower():
        fail("unexpected https error: %r" % err)
    print("https via keychain: got bytes, sniff rejected ico (ok)")

    # 3) 真 PNG 的 HTTPS：试几个公共地址，有一个过 sniff 就算。
    https_png_ok = False
    for u in (
        "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png",
        "https://github.githubassets.com/favicons/favicon.png",
    ):
        blob, err = ps._fetch_img_url(u)
        if blob and ps._sniff_img(blob):
            r = ps.save_img({"路径": "a.md", "图片": {"网址": u}})
            if not r.get("ok"):
                fail("https png save failed: %r" % r)
            https_png_ok = True
            print("https png save:", u, "->", r.get("相对路径"))
            break
        print("https png skip:", u, "->", err)
    if not https_png_ok:
        print("WARN: no public https png downloaded; keychain path still proven by favicon")

    # 4) 飞书那种「字 + 两张 http 图」：HTML 拆段顺序 + 落到 _图/
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            if self.path.endswith(".png"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(PNG_1x1)
            else:
                self.send_response(404)
                self.end_headers()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    u1 = "http://127.0.0.1:%d/a.png" % port
    u2 = "http://127.0.0.1:%d/b.png" % port
    sample = (
        "<html><body><!--StartFragment-->"
        "<div>1、</div>"
        "<img src=\"%s\" width=\"800\" height=\"400\">"
        "<div>这个表按照命名的标签进行分类</div>"
        "<img src=\"%s\" data-origin-src=\"%s\">"
        "<div>2、结尾</div>"
        "<!--EndFragment--></body></html>"
    ) % (htmlmod.escape(u1, quote=True), htmlmod.escape(u2, quote=True),
         htmlmod.escape(u2, quote=True))
    pieces = html_clipboard_pieces(sample)
    kinds = [p["kind"] for p in pieces]
    if kinds != ["text", "img", "text", "img", "text"]:
        fail("piece order: %r" % pieces)
    if "1、" not in pieces[0]["text"] or "结尾" not in pieces[4]["text"]:
        fail("text pieces: %r" % pieces)
    if pieces[1]["src"] != u1 or pieces[3]["src"] != u2:
        fail("img src: %r" % pieces)
    rels = []
    for p in pieces:
        if p["kind"] != "img":
            continue
        r = ps.save_img({"路径": "a.md", "图片": {"网址": p["src"]}})
        if not r.get("ok"):
            fail("mixed save failed: %r" % r)
        rels.append(r["相对路径"])
    if len(rels) != 2 or not all(x.startswith("_图/") for x in rels):
        fail("mixed rels: %r" % rels)
    for rel in rels:
        path = os.path.join(vault, rel)
        if not os.path.isfile(path) or open(path, "rb").read() != PNG_1x1:
            fail("mixed file missing: %s" % rel)
    httpd.shutdown()
    print("mixed html+http: 2 images in order")

    # 6) AES-GCM（飞书 CDN 正文）走系统 CommonCrypto
    key = b"\x11" * 32
    nonce = b"\x22" * 12
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        enc = AESGCM(key).encrypt(nonce, PNG_1x1, None)
    except Exception:
        from Crypto.Cipher import AES
        c = AES.new(key, AES.MODE_GCM, nonce=nonce)
        ct, tag = c.encrypt_and_digest(PNG_1x1)
        enc = ct + tag
    pt = ps._aes_gcm_decrypt(key, nonce, enc)
    if pt != PNG_1x1:
        fail("aes-gcm decrypt mismatch")
    print("aes-gcm ok")

    # 7) 飞书 native-resource → static-resource
    native = (
        "native-resource://sdk/image?resource_type=image"
        "&key=img_v3_0215l_8c4bdadf-f305-4f6c-8dd0-68412130f1eg_MIDDLE_WEBP"
        "&crypto=CAESMAog2vZN00QeSdo6v4T4TSZZZA7QuWycRdplPfxOPgUd1WISDASV%2BK3uvnCt1v1Wyw%3D%3D"
    )
    nu, crypto = ps._lark_normalize_img_url(native)
    if "img_v3_0215l_8c4bdadf-f305-4f6c-8dd0-68412130f1eg" not in nu:
        fail("normalize key: %r" % nu)
    if "MIDDLE_WEBP" in nu:
        fail("MIDDLE_WEBP leaked: %r" % nu)
    if not crypto:
        fail("crypto missing")
    print("lark url normalize ok")

    # 8) 真实飞书剪贴板 HTML（本机刚复制的那条）拆出两张 native-resource 图
    fixture = os.path.join(HERE, "fixtures", "feishu-clip.html")
    if os.path.isfile(fixture):
        real = open(fixture, encoding="utf-8").read()
        pieces = html_clipboard_pieces(real)
        imgs = [p for p in pieces if p["kind"] == "img"]
        if len(imgs) != 2:
            fail("feishu html imgs: %r" % pieces)
        if not all(p["src"].startswith("native-resource:") for p in imgs):
            fail("feishu src not native-resource: %r" % imgs)
        # 真去拉 + 解密 + 落盘（这台机器上 CDN 通）
        rels = []
        for p in imgs:
            r = ps.save_img({"路径": "a.md", "图片": {"网址": p["src"]}})
            if not r.get("ok"):
                fail("feishu save failed: %r src=%s" % (r, p["src"][:80]))
            rels.append(r["相对路径"])
            path = os.path.join(vault, r["相对路径"])
            kind = ps._sniff_img(open(path, "rb").read(16))
            if kind not in ("png", "jpg"):
                fail("feishu saved not image: %s %s" % (r["相对路径"], kind))
        print("feishu live save:", rels)
    else:
        print("WARN: no scripts/fixtures/feishu-clip.html")

    # 5) 装饰小图丢掉
    deco = html_clipboard_pieces(
        '<div>hi</div><img src="x.png" width="16" height="16" class="emoji">')
    if any(p["kind"] == "img" for p in deco):
        fail("deco img kept: %r" % deco)
    print("emoji skipped")

    print("ok urllib=%s https_png=%s" % (urllib_https, https_png_ok))


if __name__ == "__main__":
    main()
