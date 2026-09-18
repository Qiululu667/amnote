#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""贴图：base64 / 本地文件 / http 网址三条路，外加拒绝非图片、非 http。"""
import base64
import http.server
import os
import struct
import sys
import tempfile
import threading
import zlib

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


def png_red():
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = zlib.compress(b"\x00\xff\x00\x00\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def main():
    tmp = tempfile.mkdtemp(prefix="amnote-img-")
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
        f.write("# a\n")

    blob = png_red()
    if not ps._sniff_img(blob) == "png":
        raise SystemExit("sniff png failed")
    if ps._sniff_img(b"not-an-image"):
        raise SystemExit("sniff should reject text")

    r = ps.save_img({"路径": "a.md", "图片": {"数据": base64.b64encode(blob).decode("ascii")}})
    if not r.get("ok") or not r.get("相对路径", "").startswith("_图/"):
        raise SystemExit("base64 save failed: %r" % r)
    p1 = os.path.join(vault, r["相对路径"])
    if not os.path.isfile(p1) or open(p1, "rb").read() != blob:
        raise SystemExit("base64 file mismatch")

    src = os.path.join(tmp, "from-disk.png")
    with open(src, "wb") as f:
        f.write(blob)
    r = ps.save_img({"路径": "a.md", "图片": {"文件": src}})
    if not r.get("ok"):
        raise SystemExit("file save failed: %r" % r)

    r = ps.save_img({"路径": "a.md", "图片": {"文件": os.path.join(tmp, "nope.png")}})
    if r.get("ok") or r.get("代码") not in ("gone", "bad_type"):
        # 不在的文件走「这个文件不是图片」
        if r.get("ok"):
            raise SystemExit("missing file should fail: %r" % r)

    txt = os.path.join(tmp, "x.txt")
    with open(txt, "w") as f:
        f.write("hello")
    r = ps.save_img({"路径": "a.md", "图片": {"文件": txt}})
    if r.get("ok"):
        raise SystemExit("text file should fail: %r" % r)

    r = ps.save_img({"路径": "a.md", "图片": {"网址": "file://" + src}})
    if r.get("ok"):
        raise SystemExit("file: url should fail: %r" % r)

    r = ps.save_img({"路径": "a.md", "图片": {"网址": "javascript:alert(1)"}})
    if r.get("ok"):
        raise SystemExit("javascript url should fail: %r" % r)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            if self.path == "/ok.png":
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(blob)
            elif self.path == "/plain":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"hello")
            else:
                self.send_response(404)
                self.end_headers()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    r = ps.save_img({"路径": "a.md", "图片": {"网址": "http://127.0.0.1:%d/ok.png" % port}})
    if not r.get("ok"):
        raise SystemExit("http save failed: %r" % r)
    r = ps.save_img({"路径": "a.md", "图片": {"网址": "http://127.0.0.1:%d/plain" % port}})
    if r.get("ok"):
        raise SystemExit("http text should fail: %r" % r)
    r = ps.save_img({"路径": "a.md", "图片": {"网址": "http://127.0.0.1:%d/missing.png" % port}})
    if r.get("ok"):
        raise SystemExit("http 404 should fail: %r" % r)
    httpd.shutdown()
    print("ok")


if __name__ == "__main__":
    main()
