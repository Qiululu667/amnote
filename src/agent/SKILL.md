---
name: amnote
description: "Search, read and write the user's local AM·Note markdown vault through the amnote CLI. 当用户提到笔记、AM·Note、「我记过 / 我写过 / 我在笔记里」，或者要查笔记、记一笔、改一份笔记时，用这个技能，不要自己去 grep 文件夹。"
---
<!-- amnote-skill v1 -->

# AM·Note 笔记库

用户的笔记在一个本地文件夹里，AM·Note 给它建了全文索引。命令行工具是
`{{AMNOTE_BIN}}`。路径一律是**库相对路径**（`工作手记/发布检查表.md`），
不是绝对路径。

## 先看地图，再搜，最后只读要的那一节

```sh
{{AMNOTE_BIN}} map                          # 有哪些目录、每篇讲什么
{{AMNOTE_BIN}} map --dir 工作手记            # 展开某个目录
{{AMNOTE_BIN}} search "报销 流程" -n 10      # 空格分开＝AND
{{AMNOTE_BIN}} outline 工作手记/报销.md      # 这份有哪些小节
{{AMNOTE_BIN}} read 工作手记/报销.md --section 发票
```

`search` 每条命中下面是片段，`L12 [打包] …前【中】后…` 里的 `L12` 是行号、
`[打包]` 是所在小节——照着它 `read --section 打包`，别整篇读回来。
筛选：`--dir 目录`、`--type md,pdf`、`--since 7`（最近 7 天）或 `--since 2026-09-01`。

其他几条：`{{AMNOTE_BIN}} recent --days 7`（最近改了什么）、
`{{AMNOTE_BIN}} links <路径>`（谁引用了它）、`{{AMNOTE_BIN}} tree`（全部文件）。

## 写笔记：读 → 改 → 整篇写回

```sh
{{AMNOTE_BIN}} new "会议纪要 0906" <<'EOF'   # 不给 --dir 就放随手记目录
# 会议纪要 0906
…
EOF
{{AMNOTE_BIN}} new "月度复盘" --dir 工作手记 --body-file /tmp/draft.md
{{AMNOTE_BIN}} read 工作手记/报销.md > /tmp/new.md    # ← 先读，这一步不能省
{{AMNOTE_BIN}} save 工作手记/报销.md --body-file /tmp/new.md   # 改完整篇覆写
{{AMNOTE_BIN}} trash 随手记/写错了.md          # 挪进废纸篓，不是删掉
```

`save` 是**整篇覆写**，而且**必须先 `read` 过这一份**：`read` 会记下你看到的
是哪一版，`save` 拿它当「基于」交给服务端做冲突检测。没读过就 `save` 会退出码
1，说「先 amnote read 这份，或加 `--based`/`--force`」——那不是啰嗦，是在拦
「不知道原来写了什么就一把盖掉」。手上已经有那一版的「改于」（`read --json`
里那个字段）也可以直接 `--based "2026-09-06 17:20:31"`。

退出码 3 ＝ 这份在你读完之后被别处改过了。这时**先停下告诉用户**，
重新 `read` 一遍合并；确实要盖掉那次改动才加 `--force`。

写请求会记进变更流水并署上你的名字，用户在 AM·Note 里看得到是谁改的。

## 几条规矩

- 不要 `grep` / `find` 笔记文件夹，也不要直接改文件——搜索走索引，写走 `save`，
  这样才有备份、流水和冲突检测。
- `.amnote/` 是 AM·Note 自己的目录（索引、备份、配置），不看也不动。
- 退出码：0 成功 · 1 用法错（含「还没 read 过」）· 2 AM·Note 没在运行 ·
  3 冲突 · 4 服务端拒绝。
  遇到 2：只读的几条会自动用上一次的索引（结果可能旧一点），写不了，
  请用户打开 AM·Note。
- 每条命令都能加 `--json`，要结构化结果时用它。
- 不确定用户指的是哪一份时，先 `search` 列几条给用户挑，别猜着改。
