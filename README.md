# mixology-tui

Terminal UI for downloading DJ audio from SoundCloud. Supports Spotify playlist import, direct SoundCloud URLs, and 1001tracklists set pages.

## Requirements

ffmpeg must be on your PATH.
- macOS: `brew install ffmpeg`
- Windows: download from ffmpeg.org and add to PATH

Python packages are installed automatically by the launch scripts below.

## Running

### macOS — double-click `launch-macos.command`

The first time only, macOS will block the script because it is not from a signed developer. To allow it:

1. Right-click `launch.command` in Finder and choose **Open**.
2. Click **Open** in the dialog that appears.
3. From then on, double-clicking works normally.

Each launch pulls the latest changes from the repository (if this folder is a git clone), installs or updates packages, then starts the app.

If you cloned via SSH and do not have keys set up on this machine, switch the remote to HTTPS first:

```
git remote set-url origin https://github.com/your-username/mixology-tui.git
```

### Windows — double-click `launch-windows.bat`

Double-click `launch-windows.bat`. A Command Prompt window opens, pulls updates, installs packages, and starts the app. The window stays open if the app exits with an error.

### Manual

```
python3 mixology-tui.py          # macOS / Linux
python  mixology-tui.py          # Windows
```

Navigate tabs with **F1–F6** or by clicking the tab bar. Quit with **Ctrl+Q**.

---

## Tabs

### F1 — Spotify

Fetch a Spotify playlist by URL and download selected tracks via SoundCloud.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Paste Spotify playlist URL…                           │ Fetch             │
├──────────────────────────────────────────────────────────────────────────┤
│ My Deep House Playlist  (47 tracks)                                      │
│                                                                          │
│ [ Select All ] [ None ]                                                  │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ ▣  Seven Lions — Rush Over Me  [4:12]                             │   │
│ │ ▣  ILLENIUM — Beautiful Creatures  [3:58]                         │   │
│ │ ▢  Dog Blood — Break Law  [3:44]                                  │   │
│ │ ▣  Subtronics — Woah  [4:30]                                      │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│ [ Download Selected                                                     ] │
└──────────────────────────────────────────────────────────────────────────┘
```

1. Paste a Spotify playlist URL and click **Fetch**.
2. Check or uncheck individual tracks.
3. Click **Download Selected**. Each track is searched on SoundCloud by artist and title.

Requires Spotify API credentials — see Settings (F5).

---

### F2 — SC Search

Search SoundCloud by query string and download a specific result.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Search SoundCloud…                                    │ Search            │
├──────────────────────────────────────────────────────────────────────────┤
│ 8 results for seven lions rush over me                                   │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │  #   Artist                  Title                          Dur   │   │
│ │  1   Seven Lions             Rush Over Me                  4:12   │   │
│ │  2   ILLENIUM                Rush Over Me (Remix)          4:44   │   │
│ │  3   DJSnake                 Rush Over Me (Cover)          3:31   │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│ [ Download highlighted track                                            ] │
└──────────────────────────────────────────────────────────────────────────┘
```

Use arrow keys to move between results. Click **Download highlighted track** to queue the selected row.

---

### F3 — Direct URL

Paste any SoundCloud URL and download it immediately.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Download any SoundCloud track, set, or playlist directly.                │
│                                                                          │
│ https://soundcloud.com/…                              │ Download          │
└──────────────────────────────────────────────────────────────────────────┘
```

Accepts track URLs, set URLs, and playlist URLs. Sets and playlists are expanded by yt-dlp and each track is queued individually.

---

### F4 — Queue

Live view of all downloads in this session.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Status           Track                                Progress / Error   │
│ ✓  done          Seven Lions — Rush Over Me                              │
│ ↓  loading       ILLENIUM — Beautiful Creatures       72%  1240 KB/s     │
│ ⏳ queued        Dog Blood — Break Law                                   │
│ ⏳ queued        Subtronics — Woah                                       │
│ ✗  failed        Some Track                           Not found on SC    │
│                                                                          │
│ 1 / 5 complete   ·   ILLENIUM — Beautiful Creatures… 72%  1240 KB/s     │
└──────────────────────────────────────────────────────────────────────────┘
```

Downloads run sequentially. Status and progress update in place. The footer shows overall completion and the active download's speed. Tracks that cannot be found on SoundCloud are marked failed and skipped.

---

### F5 — Settings

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Output directory                                                         │
│ /Users/you/Music/DJ                                                      │
│                                                                          │
│ Spotify API                                                              │
│ Client ID      [                                                       ] │
│ Client Secret  [                                               ········] │
│                                                                          │
│ [ Save ] [ Login with Spotify ]                                          │
│ Logged in to Spotify                                                     │
│                                                                          │
│ SoundCloud Go+ token  (optional — unlocks 256 kbps AAC)                 │
│ [                                                              ········] │
└──────────────────────────────────────────────────────────────────────────┘
```

**Output directory** — where downloaded files are saved. Defaults to `~/Music/DJ`.

**Spotify API** — create an app at developer.spotify.com, add `http://127.0.0.1:8888/callback` as a redirect URI, paste the Client ID and Secret here, then click **Login with Spotify**. A browser window opens for OAuth. The token is cached at `~/.config/mixology-tui/.spotify_cache` and reused across sessions.

**SoundCloud Go+ token** — optional. Unlocks 256 kbps AAC downloads instead of the default 160 kbps. Find it in browser DevTools under any SoundCloud network request's Authorization header value (starts with `OAuth`).

---

### F6 — 1001TL

Download a tracklist from a 1001tracklists.com DJ set.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ In your browser: open the set page → Ctrl+S → save as HTML.             │
│                                                                          │
│ /Downloads/Seven Lions @ EDC 2026.html  │ Browse… │ Parse               │
│ 50 identified tracks  (1 unidentified skipped by default)               │
│                                                                          │
│ [ Select All ] [ None ]                                                  │
│ ┌────────────────────────────────────────────────────────────────────┐   │
│ │ ▣  Seven Lions - A Crown Of Seven Moons                          │   │
│ │ ▣  Seven Lions & Brieanna Grace - Free                           │   │
│ │ ▣  Excision & Gryffin ft. Julia Michaels - Air (NEOTEK Remix)   │   │
│ │ ▢  ID (unidentified)                                             │   │
│ │ ▣  Above & Beyond ft. Zoe Johnston - Sahara Love                │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│ Download folder:  [ seven-lions-edc-2026                              ]  │
│ [ ] Number tracks in set order (01 - ..., 02 - ...)                      │
│ [ Download Selected                                                     ] │
└──────────────────────────────────────────────────────────────────────────┘
```

**Why save the page manually?** 1001tracklists uses Cloudflare bot protection that blocks automated HTTP requests. Saving from your browser captures the fully-rendered page, bypassing this entirely — no extra tools needed.

Steps:

1. Open the set page in your browser.
2. Save with **Ctrl+S** (Cmd+S on Mac). When prompted, choose "Webpage, HTML Only".
3. In the app, click **Browse** and select the saved file, or paste the path and press Enter.
4. Click **Parse**. Identified tracks load pre-checked; unidentified (ID) tracks load unchecked.
5. Set a **Download folder** name. Pre-filled from the filename — edit as needed.
6. Optionally enable **Number tracks in set order** to prepend a zero-padded index to each filename, preserving play order in Finder and Rekordbox.
7. Click **Download Selected**. The app switches to the Queue tab automatically.

---

## Output

Files are named using SoundCloud's uploader name and track title:

```
~/Music/DJ/
  Seven Lions - A Crown Of Seven Moons.m4a
  Excision & Gryffin - Air.m4a
```

With a subfolder and set-order numbering (1001TL tab):

```
~/Music/DJ/seven-lions-edc-2026/
  01 - Seven Lions - A Crown Of Seven Moons.m4a
  02 - Seven Lions & Brieanna Grace - Free.m4a
  03 - Excision & Gryffin - Air (NEOTEK Remix).m4a
  05 - Above & Beyond - Sahara Love (Seven Lions Remix).m4a
```

Numbering reflects the original set position. Gaps in the sequence indicate tracks that were unchecked or marked as unidentified.

## Audio quality

Format priority:

1. Original upload — WAV or FLAC, if the artist enabled it on SoundCloud
2. 256 kbps AAC — requires a SoundCloud Go+ OAuth token in Settings
3. 160 kbps AAC — available without authentication

After each download, ffmpeg rewrites the embedded metadata (artist, title, artwork) from SoundCloud's own track data, replacing whatever the uploader originally embedded.
