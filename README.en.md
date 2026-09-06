<p align="center">
  <a href="README.md">简体中文</a> ·
  <a href="README.zh-HK.md">繁體中文（香港）</a> ·
  <b>English</b>
</p>

<p align="center">
  <img src="src/icon-192.png" width="96" alt="AM·Note">
</p>

<h1 align="center">AM·Note</h1>

<p align="center">
  A local Markdown note browser for macOS.<br>
  Point it at any folder on your Mac and search, read and edit it like a notebook.<br>
  Your files stay where they are. Nothing moves, nothing is uploaded.
</p>

<p align="center">
  <a href="https://github.com/Qiululu667/amnote/releases"><img alt="Download" src="https://img.shields.io/github/v/release/Qiululu667/amnote?label=Download&color=3279c7"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/License-MIT-lightgrey"></a>
  <img alt="macOS 12+" src="https://img.shields.io/badge/macOS-12%2B-black">
</p>

## The problem it solves

You already have a pile of `.md` files, plus a few `.html` pages exported from somewhere else. They sit in Downloads, on the Desktop, inside some project folder, each minding its own business.

To find one sentence you first have to remember which folder it's in. To fix one paragraph you have to pick an editor and open it. A cloud notes app would happily hold all of it — but only after you move the files in, and once they're in, they aren't your files any more.

AM·Note doesn't move anything. Pick a folder and you get a window as light as a browser: ⌘K searches that folder, and the tabs show the very same files. When you're done editing, they're still the same files.

![Start page: folder chips, Continue reading and Recently opened](docs/en/home.png)

## How it works

1. The first time you open it, pick a folder — or let it make one for you. Any folder will do, including an empty one.
2. It creates one `.amnote/` folder inside for the index and edit backups. Not a single byte of your notes is touched.
3. The window is two rows: tabs, search and settings on top; below them, where this file lives and what you can do with it. Every note gets its own **tab**. Press **⌘K** to search the whole folder.

## What you can do

**Find**

- ⌘K is the one and only way in. Start typing and the first row reads "Search all notes for “…”" — Return searches the full text, ⌘ Return searches in a new tab.
- Under that first row are actions and note titles. Arrow keys to choose, Return to open. It matches file names and body text, in this folder — not on the web.
- You can also just type on the Start page; the first keystroke brings the panel up.
- Below the greeting on the Start page is a row of folder chips: the top-level folders in your vault, with "Pinned" last. Click one to see what's inside, ⌘-click to open it in a new tab.
- Cards can be filtered by "All / Markdown / HTML", and switched between grid and list.

**Read**

- Both .md and .html open in tabs. Web pages in your vault render as they are — no conversion.
- The Start page has "Recently opened" plus a "Continue reading" card, so the note you were last in is right there.
- Next to "My notes" on the Start page is a small dot that usually reads "Ready · 128 notes", and "Indexing…" while a scan is running. There's no status bar at the bottom of the window any more.
- Click the ★ on a card to pin it; everything you pin lives behind the "Pinned" chip.
- The row above the text is the document header: on the left, the outline toggle and this file's path — click the path to copy it, ⌘-click one of the folder segments to open that folder in a new tab. On the right: pin, Reveal in Finder, Share, Open in New Window, then focus, "⋯" and "Edit".
- The outline sits on the left: heading levels at a glance, the section you're reading highlights itself, click to jump. ⌥⌘I hides it or brings it back.
- Focus mode: click ⤢ and the document header and outline slide away, leaving only the text. Nudge the top of the window with the pointer to bring them back; Esc leaves.

![Reading a note: two rows on top, outline on the left](docs/en/reading.png)

**Write**

- Double-click the text to start editing, with the cursor in the paragraph you clicked. ⌘E works too.
- Select text and a format bar floats up: bold, headings, lists, quotes, links, tables, add row, add column.
- Stop typing for two seconds and it saves, with "Saved 14:29" in the top right. ⌘S saves right now; click "Done" when you're finished.
- If the file was changed in another editor meanwhile, saving stops to ask, and you choose "Keep Mine" or "Use Theirs".
- Click "View Markdown source" to edit the whole file as source, then switch back.
- Paste a screenshot straight into the text; the image lands in `_图/` ("images") next to that note.

![Editing the same note: format bar and "Done"](docs/en/editing.png)

**Organize**

- ⌘T opens a new tab on the Start page. Click "New Quick Note" and write a first heading — the file renames itself to match.
- ⌘⇧T brings back the tab you just closed.
- Reveal in Finder, Share and Open in New Window are the small icons on the right of the document header; to copy the path, just click the path. Print and "Open Enclosing Folder" live under "⋯".
- A quick note you didn't mean to make: "⋯" → Move to Trash, or press ⌘⌫. You can undo from the toast for a few seconds, and it's in the system Trash either way.
- Double-click a `.md` in Finder to open it here.

**Settings**

- ⌘, opens it, or click the ⚙ in the top right. Four panes: Appearance, Vault, Advanced, About.
- Appearance: **interface language** (System / 简体中文 / 繁體中文（香港） / English — the menus and dialogs of the app switch with it), theme (System / Light / Dark), reading font, text size 15–21, line spacing Compact 1.6 / Comfortable 1.8 / Loose 2.0, column width Narrow 620 / Regular 690 / Wide 820, and "Show the outline when a note opens". Switch to dark and the title bar goes with it — window and page are one piece.
- **Five reading fonts**, each previewed on its own card: System, Serif, Monospace, and new in 5.5 **PingFang HK** (with six weights, from Ultralight to Semibold) and **PMingLiU**. If PMingLiU isn't installed, AM·Note borrows it from a copy of Microsoft Office you already have; without Office it falls back to Songti TC.
- Vault: which folder you're using, open it in Finder or change it; how many notes were indexed and when it last ran, plus a button to run it again; where quick notes go; which folders to leave out.
- Advanced is folded away by default — skipped files and the local service port are in there. About has the version, "Check for Updates" and the automatic-update switch.

## What it doesn't do

- No sync, no upload, no account. It's fully offline, except when you press "Check for Updates".
- It doesn't copy your files or reorganize your folders.
- It doesn't lock you in. The same file can be opened in another editor any time, and you can use both at once (it will tell you when they disagree).
- It only renders `.md` and `.html`. Other file types are still indexed and searchable, but they aren't listed in the window and aren't rendered.

## Install

Download `AMNote-mac.zip` from [Releases](https://github.com/Qiululu667/amnote/releases) and unzip it to get `AM·Note.app`.

**The first time, right-click the icon → Open**, don't double-click. There's no paid Apple signature, so macOS asks once; click Open and you're through.

Requires macOS 12 or newer. No account, no network.

To build it yourself: run `python3 src/build_app.py` in the repo root; the app lands in `dist/`.

## The first launch

1. Right-click `AM·Note.app` → Open.
2. A welcome card offers two choices: **Create a Vault**, which makes an `AM·Note` folder in Documents with a welcome note inside; or **Use an Existing Folder**, which lets you pick a folder that already holds Markdown files or web pages.
3. It indexes once, tells you how many notes it found, and drops you on the Start page. The first time, there's also a small "New here? Three things and you're set" card — click "Got it" and it won't come back.

Your choice is remembered. Next time it opens on the Start page; it won't jump you back into the last note.

An empty folder works too: you can search (0 results) and create quick notes. New quick notes go into `随手记/` ("quick notes") inside the folder you chose, created on demand.

If that folder gets moved or deleted, AM·Note doesn't quit — it just asks whether you want to "Choose Another Folder…" or "Create a Vault". Your files are fine; it simply can't find the folder.

Want to try it first? Point it at [`examples/demo-vault/`](examples/demo-vault) in this repo.

## Switching folders

Settings (⌘,) → Vault → "Change Folder…". The menu item Vault → Choose Folder… does the same thing. The old folder and its `.amnote/` stay untouched, so switching back picks up where you left off.

## Updates

AM·Note → Check for Updates…, or the same button on the About pane of Settings. A new version installs in place: the app quits, reopens, and your notes are still in the same folder.

From the second launch on, it asks GitHub Releases about once a day. "Check for Updates Automatically" can be turned off in the menu or in Settings. It only asks for a version number and a package — it never uploads anything.

If you're on an older build, you'll need to download a version with "Check for Updates" once by hand (5.1.0 and later); after that you're done with GitHub.

## Keyboard shortcuts

| Key | |
| --- | --- |
| ⌘T | New Tab (Start page) |
| ⌘N | New Window |
| ⌘W | Close Tab (closes the window when only the Start page is left) |
| ⌘⇧T | Reopen the tab you just closed |
| ⌘K | Search / Quick Open |
| ⌘L, ⌥⌘K | Also open the same search panel |
| ⌘E | Start Editing |
| ⌘S | Save |
| ⌘⌫ | Move to Trash |
| ⌘[ / ⌘] | Back / Forward |
| ⌃Tab | Switch tabs |
| ⌥⌘I | Show Outline |
| ⌘F | Find on Page |
| ⌥⌘O | Open in New Window |
| ⇧⌘R | Reveal in Finder |
| ⌥⌘C | Copy Path |
| ⌘, | Settings |
| Esc | Dismiss overlay / leave focus / stop editing |

The full list is in the app: Help → Keyboard Shortcuts.

## Where everything lives

Your notes stay in the folder you chose. The only thing added is one `.amnote/`:

| | |
| --- | --- |
| `fulltext.db` | The full-text index |
| `changes.jsonl` | A change log (who touched which file, and when) |
| `backups/` | Edit backups — the last 10 versions of each file |
| `config.json` | This vault's settings (skipped folders, port range, quick-notes folder…) |

Delete `.amnote/` and every note is still there; the next launch just scans again.

- Quick notes go into `随手记/` ("quick notes") inside the vault. The folder name is editable in Settings → Vault.
- Images pasted into the text land in `_图/` ("images") next to that .md file.
- The service's port and token live in `~/Library/Application Support/AMNote/` as `portal.port` and `portal.token` (the token file is 0600).

## A local API for agents and scripts

While the app is running, anything on this Mac can search the vault directly. The service listens on `127.0.0.1` only, picks a port between 8870 and 8900 by default, and **never emits CORS headers** — a web page in a browser cannot reach it.

Read routes need no token. Write routes (every POST) need `X-AMN-Token` in the header.

The JSON field names are Chinese — that's the wire format, so pass them through as they are.

```bash
D=~/Library/Application\ Support/AMNote
P=$(cat "$D/portal.port")
T=$(cat "$D/portal.token")

# Check the service is alive first. Anything but 200 means don't retry —
# search the folder directly instead.
curl -s -m 3 -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$P/__status"

# Search the whole vault. Non-ASCII terms need --data-urlencode;
# n defaults to 200, with a ceiling of 500.
curl -sG "http://127.0.0.1:$P/__search" \
     --data-urlencode "q=检查表" --data-urlencode "n=10"

# What's in the vault: the folder tree, every md/html, and quick notes
curl -s "http://127.0.0.1:$P/__tree"

# Read one .md as source
curl -sG "http://127.0.0.1:$P/__raw" --data-urlencode "path=工作手记/发布检查表.md"

# Write it back. Needs the token. Put the "改于" you last read into "基于";
# if someone else changed the file meanwhile, the write is refused.
curl -s -X POST "http://127.0.0.1:$P/__save" -H "X-AMN-Token: $T" \
     --data-binary '{"路径":"工作手记/发布检查表.md","正文":"# 标题\n\n正文\n","基于":"2026-09-05 09:42:00"}'

# Move a file to the system Trash. It only moves, never edits;
# accepts .md / .html / .htm
curl -s -X POST "http://127.0.0.1:$P/__trash" -H "X-AMN-Token: $T" \
     --data-binary '{"路径":"工作手记/建错了.md"}'

# Put it back where it was. The undo table lives in memory only — after a
# restart there's nothing to undo, so go to the Trash instead.
curl -s -X POST "http://127.0.0.1:$P/__untrash" -H "X-AMN-Token: $T" \
     --data-binary '{"路径":"工作手记/建错了.md"}'
```

`/__save` is the only route that changes a file's contents. It writes `.md` files inside the vault only, and backs one up before writing. The other two, `/__trash` and `/__untrash`, just move files in and out of the system Trash byte for byte. Backup folders, cache folders and hidden folders are all off limits.

## FAQ

**Double-clicking won't open it — macOS says the file is damaged or can't be verified.** There's no Apple signature. Right-click the icon → Open; macOS asks once, and you click Open.

**Where's the index?** In `.amnote/` inside the folder you chose. It isn't indexed itself and can't be found by search.

**What happens if I delete `.amnote/`?** Every note is still there. The next launch scans again; edit backups and the change log are gone.

**Can I keep several vaults?** One vault per window. Settings → Vault → "Change Folder…" switches over; each folder keeps its own `.amnote/`, so switching back resumes where you were.

**Will it fight with Obsidian or another editor?** It won't overwrite. When you save, if the file was changed elsewhere, a bar appears at the top and you choose "Keep Mine" or "Use Theirs".

**Does it need the internet?** No. Only checking for updates reaches out to GitHub.

## License

[MIT](LICENSE)
