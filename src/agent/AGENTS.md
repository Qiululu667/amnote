<!-- 由 AM·Note 生成。这个文件夹是一个笔记库。 -->

# 这个文件夹是 AM·Note 的笔记库

Notes vault managed by AM·Note. Use the `amnote` CLI below instead of grepping
these files: it goes through the full-text index and keeps backups, a change
log and conflict detection.

命令行工具：`{{AMNOTE_BIN}}`。路径一律是库相对路径（`工作手记/报销.md`）。

```sh
{{AMNOTE_BIN}} map                          # 有哪些目录、每篇讲什么
{{AMNOTE_BIN}} search "报销 流程" -n 10      # 全文搜索，空格分开＝AND
{{AMNOTE_BIN}} outline 工作手记/报销.md      # 这份有哪些小节
{{AMNOTE_BIN}} read 工作手记/报销.md --section 发票
{{AMNOTE_BIN}} recent --days 7               # 最近改了什么
{{AMNOTE_BIN}} new "会议纪要 0906"           # 正文从 stdin 读
{{AMNOTE_BIN}} read 工作手记/报销.md > /tmp/new.md   # 改之前先整篇读出来
{{AMNOTE_BIN}} save 工作手记/报销.md --body-file /tmp/new.md
```

规矩：

- 别 `grep` 这个文件夹，也别直接改文件——搜索走索引，写走 `save`。
- `save` 是整篇覆写，而且**先 `read` 过才写得了**：`read` 记下你看到的那一版，
  `save` 拿它做冲突检测。没读过就写退出码 1（要么 `--based <改于>`，
  要么 `--force`）。退出码 3 ＝ 这份在别处被改过，先停下问用户，别直接 `--force`。
- `.amnote/` 是 AM·Note 自己的目录（索引、备份、配置），不看也不动。
- `库地图.md` 是导出的地图，会被覆盖重写，别在里面手写东西。
- 退出码：0 成功 · 1 用法错 · 2 AM·Note 没在运行 · 3 冲突 · 4 服务端拒绝。
