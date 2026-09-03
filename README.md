<p align="center">
  <img src="src/icon-192.png" width="96" alt="AM·Note">
</p>

<h1 align="center">AM·Note</h1>

<p align="center">把本机一个文件夹当成笔记库来用。<br>文件留在原地，不会搬家，也不会上传到网上。</p>

<p align="center">
  <a href="https://github.com/Qiululu667/amnote/releases"><img alt="下载" src="https://img.shields.io/github/v/release/Qiululu667/amnote?label=下载&color=3279c7"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/许可-MIT-lightgrey"></a>
  <img alt="macOS 12+" src="https://img.shields.io/badge/macOS-12%2B-black">
</p>

已经有一堆 Markdown、不想再搬进云笔记的时候，把它指到那个文件夹就行。打开、搜索、改一改，还是原来那些文件。

![首页：搜索整个库](docs/home.png)

## 能做什么

- **搜整个文件夹。** 首页一个搜索框，⌘K 按名字直达。
- **在 App 里读写。** 双击进编辑，停笔两秒自动保存；⌘N 直接落到一篇新笔记，不用先弹命名框。
- **文件还在访达里。** 不会复制一份到别处，也不会同步到网上。改名、备份、用别的编辑器打开，都随你。
- **浅色 / 深色 / 跟随系统。** 跟 macOS 标题栏是一块的。
- **给 Agent 留了口。** 软件开着时，本机可以搜库（见下面）。

## 需要什么

macOS 12 或更新。不用账号。日常不用联网；检查更新时会访问 GitHub。

## 安装

- **给别人用：** 到 [Releases](https://github.com/Qiululu667/amnote/releases) 下载 `AMNote-mac.zip`，解开得到 `AM·Note.app`。
- **自己编：** 仓库根目录跑 `python3 src/build_app.py`，编完的 app 在 `dist/`。

没花钱做苹果签名。第一次请 **右键图标 → 打开**，不要双击。系统会问一次，点开就行。

## 第一次打开

1. 右键 `AM·Note.app` → 打开。
2. 选一个要索引的文件夹。取消就退出。
3. 选完会记住，下次直接进。

空文件夹也能用：能搜（0 条），能新建随手记。随手记默认落在所选文件夹下的 `随手记/`，没有就自动建。

想先看一眼，可以把 App 指到仓库里的 [`examples/demo-vault/`](examples/demo-vault)。

## 换一个文件夹

菜单栏「库」→「选择文件夹…」。

## 更新

菜单「AM·Note → 检查更新…」。有新版本可以直接装，装完会退出再打开，笔记还在原来的文件夹里。

默认大约一天查一次 GitHub，同一菜单里可以关掉「自动检查更新」。只问 Releases，不会上传任何东西。

已经装了旧版的，需要先手动下一次带「检查更新」的版本（5.1.0 起）；之后就不用再去 GitHub 了。

## 日常

| 快捷键 | |
| --- | --- |
| ⌘N | 新建随手记 |
| ⌘K | 按名字直达 |
| ⌥⌘K | 跳到搜索框 |
| ⌘E | 进入编辑 |
| ⌘S | 保存（平时停笔就会存） |
| ⌃⌘S | 显示列表 |
| ⌥⌘C | 拷贝路径 |
| ⇧⌘R | 在访达中显示 |
| Esc | 关浮层 / 退出编辑 |

完整列表在 App 里：帮助 → 快捷键。

## 文件在哪

你的笔记还在原来那个文件夹里。

文件夹里会多一个 `.amnote`。那是索引、流水和编辑备份，不是你的笔记。删掉它，笔记还在，只是下次打开要重新扫一遍。

## 给 Agent 用（可选）

软件开着时，端口写在 `~/Library/Application Support/AMNote/portal.port`。

先确认服务还活着，再搜。中文词要用 `--data-urlencode`。

```bash
P=$(cat ~/Library/Application\ Support/AMNote/portal.port)
curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$P/"
curl -sG "http://127.0.0.1:$P/__search" --data-urlencode "q=关键词" --data-urlencode "n=10"
```

不是 200 就不要重试，改在文件夹里直接搜。

## 许可

[MIT](LICENSE)
