# AM·Note

本地文件夹上的笔记索引。文件留在原地，不会搬家，也不会上传到网上。

## 需要什么

macOS 12 或更新。

## 安装

- 有现成的包：打开 `dist/AM·Note.app`。
- 自己编：在仓库根目录跑 `python3 src/build_app.py`。编完的 app 在 `dist/`。

## 第一次打开

没花钱做苹果签名。第一次请：

1. 右键图标 → 打开（不要双击）。
2. 选一个要索引的文件夹。取消就退出。

选完会记住，下次直接进。

## 换一个文件夹

菜单栏「库」→「选择文件夹…」。

## 文件在哪

你的笔记还在原来那个文件夹里。

文件夹里会多一个 `.amnote`。那是索引，不是你的笔记。删掉它，笔记还在。

## 给 Agent 用（可选）

软件开着时，端口写在 `~/Library/Application Support/AMNote/portal.port`。

先确认服务还活着，再搜。中文词要用 `--data-urlencode`。

```bash
P=$(cat ~/Library/Application\ Support/AMNote/portal.port)
curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$P/"
curl -sG "http://127.0.0.1:$P/__search" --data-urlencode "q=关键词" --data-urlencode "n=10"
```

不是 200 就不要重试，改在文件夹里直接搜。
