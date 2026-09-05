#!/usr/bin/env python3
"""
scdown — DJ audio downloader TUI

Install:
  python -m pip install yt-dlp textual spotipy

Run:
  python scdown.py
"""

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yt_dlp
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    SelectionList,
    TabbedContent,
    TabPane,
)
from textual.widgets.selection_list import Selection

# ─── Config ─────────────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".config" / "scdown" / "config.json"
SOUNDCLOUD_RE = re.compile(r"https?://(www\.)?soundcloud\.com/", re.IGNORECASE)
# Handle plain, locale-prefixed, and URI forms:
#   https://open.spotify.com/playlist/ID
#   https://open.spotify.com/intl-en/playlist/ID
#   spotify:playlist:ID
SPOTIFY_RE = re.compile(
    r"(?:open\.spotify\.com/(?:[^/?#]+/)?playlist/|spotify:playlist:)([\w]+)",
    re.IGNORECASE,
)
SPOTIFY_CACHE = CONFIG_FILE.parent / ".spotify_cache"
SPOTIFY_REDIRECT = "http://127.0.0.1:8888/callback"    # loopback — supported per Spotify migration guide
SPOTIFY_SCOPE = "playlist-read-private playlist-read-collaborative"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ─── Data models ────────────────────────────────────────────────────────────

@dataclass
class Track:
    title: str
    artist: str
    duration_s: int = 0

    @property
    def query(self) -> str:
        return f"{self.artist} {self.title}"

    @property
    def label(self) -> str:
        if self.duration_s:
            m, s = divmod(self.duration_s, 60)
            return f"{self.artist} — {self.title}  [{m}:{s:02d}]"
        return f"{self.artist} — {self.title}"


@dataclass
class Job:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    display: str = ""
    query: str = ""
    status: str = "queued"   # queued | searching | downloading | done | failed
    progress: str = ""
    error: str = ""
    subfolder: str = ""
    filename_prefix: str = ""  # e.g. "01 - " for set-order enumeration


# ─── Audio engine ────────────────────────────────────────────────────────────

def _ydl_base(cfg: dict) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        # Prefer original artist upload (WAV/FLAC), then highest-bitrate AAC
        "format": "bestaudio[format_id*=original]/bestaudio/best",
        "extractor_args": {"soundcloud": {"formats": ["http_aac", "hls_aac"]}},
        # Overwrite whatever tags the uploader embedded with correct SoundCloud metadata
        "postprocessors": [{"key": "FFmpegMetadata", "add_metadata": True}],
    }
    if cfg.get("sc_token"):
        opts["http_headers"] = {"Authorization": f"OAuth {cfg['sc_token']}"}
    return opts


def sc_search(query: str, cfg: dict, n: int = 8) -> list[dict]:
    opts = _ydl_base(cfg) | {"extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch{n}:{query}", download=False)
    return (info or {}).get("entries") or []


def sc_resolve_url(query: str, cfg: dict) -> Optional[str]:
    if SOUNDCLOUD_RE.match(query):
        return query
    results = sc_search(query, cfg, n=1)
    if results:
        return results[0].get("webpage_url") or results[0].get("url")
    return None


def sc_download(url: str, cfg: dict, out_dir: Path, on_progress, prefix: str = "") -> None:
    def _hook(d: dict) -> None:
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            speed = d.get("speed") or 0
            pct = f"{done / total * 100:.0f}%" if total else "…"
            spd = f"  {speed / 1024:.0f} KB/s" if speed else ""
            on_progress(f"{pct}{spd}")
        elif d["status"] == "finished":
            on_progress("Saving…")

    opts = _ydl_base(cfg) | {
        "outtmpl": str(out_dir / f"{prefix}%(uploader)s - %(title)s.%(ext)s"),
        "progress_hooks": [_hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


# ─── Spotify ─────────────────────────────────────────────────────────────────

def _spotify_auth(cfg: dict):
    """Return a SpotifyOAuth auth manager configured for this app."""
    try:
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        raise RuntimeError("spotipy not installed — run: python -m pip install spotipy")

    cid = cfg.get("spotify_client_id", "")
    secret = cfg.get("spotify_client_secret", "")
    if not cid or not secret:
        raise RuntimeError(
            "Spotify credentials not set. Open Settings (F5), enter your\n"
            "Client ID + Secret, save, then click 'Login with Spotify'."
        )
    return SpotifyOAuth(
        client_id=cid,
        client_secret=secret,
        redirect_uri=SPOTIFY_REDIRECT,
        scope=SPOTIFY_SCOPE,
        cache_path=str(SPOTIFY_CACHE),
        open_browser=False,
    )


def spotify_is_logged_in(cfg: dict) -> bool:
    """True if a valid (or refreshable) token is cached."""
    try:
        auth = _spotify_auth(cfg)
        token = auth.get_cached_token()
        return token is not None
    except Exception:
        return False


def spotify_login(cfg: dict) -> None:
    """Open browser for OAuth, spin up localhost:8888 to catch the callback,
    exchange the code for a token, and cache it. Raises on failure."""
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    auth = _spotify_auth(cfg)
    auth_url = auth.get_authorize_url()
    webbrowser.open(auth_url)

    received: list[Optional[str]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            received.append(params.get("code", [None])[0])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Authorized! You can close this tab.</h2>")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 8888), _Handler)
    while not received:
        server.handle_request()
    server.server_close()

    code = received[0]
    if not code:
        raise RuntimeError("Spotify authorization was denied or timed out.")
    auth.get_access_token(code, as_dict=False, check_cache=False)


def fetch_spotify_playlist(playlist_id: str, cfg: dict) -> tuple[str, list[Track]]:
    import spotipy

    auth = _spotify_auth(cfg)
    token = auth.get_cached_token()
    if not token:
        raise RuntimeError(
            "Not logged in to Spotify.\n"
            "Go to Settings (F5) and click 'Login with Spotify'."
        )
    sp = spotipy.Spotify(auth_manager=auth)

    pl = sp.playlist(playlist_id, fields="name")
    name = pl["name"]
    tracks: list[Track] = []

    results = sp.playlist_items(playlist_id, additional_types=("track",), limit=100)
    while results:
        for item in results.get("items") or []:
            t = (item or {}).get("track")
            if not t or not t.get("name"):
                continue
            artists = ", ".join(a["name"] for a in t.get("artists") or [])
            tracks.append(Track(
                title=t["name"],
                artist=artists,
                duration_s=(t.get("duration_ms") or 0) // 1000,
            ))
        results = sp.next(results) if results.get("next") else None

    return name, tracks


# ─── 1001tracklists ──────────────────────────────────────────────────────────

def pick_html_file() -> Optional[str]:
    """Open the native OS file picker. Must be called from a background thread."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select saved 1001tracklists HTML",
        filetypes=[("HTML files", "*.htm *.html"), ("All files", "*.*")],
    )
    root.destroy()
    return path or None


def parse_1001tl_html(path: Path) -> list[dict]:
    """Parse a locally-saved 1001tracklists page; return track dicts."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise RuntimeError(
            "beautifulsoup4 not installed — run: python -m pip install beautifulsoup4 lxml"
        )
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
    tracks = []
    for item in soup.find_all("div", class_="tlpItem"):
        artist_tag = item.find("meta", itemprop="byArtist")
        title_tag = item.find("meta", itemprop="name")
        artist = (artist_tag.get("content") or "").strip() if artist_tag else ""
        title = (title_tag.get("content") or "").strip() if title_tag else ""
        is_id = not artist or artist.upper() == "ID" or not title or title.upper() == "ID"
        # title is already "Artist - Track Name" from the schema.org name field
        display = title if not is_id else "ID (unidentified)"
        tracks.append({"artist": artist, "title": title, "is_id": is_id, "display": display})
    return tracks


# ─── Panes ───────────────────────────────────────────────────────────────────

class SpotifyPane(Vertical):
    """Fetch a Spotify playlist, select tracks, download."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tracks: list[Track] = []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="input-row"):
            yield Input(placeholder="Paste Spotify playlist URL…", id="sp-url")
            yield Button("Fetch", id="sp-fetch", variant="primary")
        yield Label("", id="sp-status", classes="status-line")
        with Horizontal(classes="btn-row"):
            yield Button("Select All", id="sp-all")
            yield Button("None", id="sp-none")
        yield SelectionList(id="sp-list")
        yield Button("Download Selected", id="sp-go", variant="success", disabled=True)

    @on(Button.Pressed, "#sp-fetch")
    def _on_fetch_btn(self) -> None:
        self._trigger_fetch()

    @on(Input.Submitted, "#sp-url")
    def _on_url_enter(self) -> None:
        self._trigger_fetch()

    def _trigger_fetch(self) -> None:
        url = self.query_one("#sp-url", Input).value.strip()
        m = SPOTIFY_RE.search(url)
        if not m:
            self.query_one("#sp-status", Label).update(
                "[red]Paste a valid Spotify playlist URL.[/red]"
            )
            return
        self._do_fetch(m.group(1))

    @on(Button.Pressed, "#sp-all")
    def _on_all(self) -> None:
        self.query_one(SelectionList).select_all()

    @on(Button.Pressed, "#sp-none")
    def _on_none(self) -> None:
        self.query_one(SelectionList).deselect_all()

    @on(Button.Pressed, "#sp-go")
    def _on_download(self) -> None:
        lst = self.query_one(SelectionList)
        selected_indices: list[int] = list(lst.selected)
        if not selected_indices:
            self.query_one("#sp-status", Label).update("[yellow]Nothing selected.[/yellow]")
            return
        jobs = [
            Job(display=self._tracks[i].label, query=self._tracks[i].query)
            for i in selected_indices
            if i < len(self._tracks)
        ]
        self.app.enqueue(jobs)  # type: ignore[attr-defined]

    @work(thread=True)
    def _do_fetch(self, playlist_id: str) -> None:
        self.app.call_from_thread(
            self.query_one("#sp-status", Label).update,
            "[yellow]Fetching playlist…[/yellow]",
        )
        cfg = load_config()
        try:
            name, tracks = fetch_spotify_playlist(playlist_id, cfg)
        except Exception as exc:
            from rich.markup import escape
            self.app.call_from_thread(
                self.query_one("#sp-status", Label).update,
                f"[red]{escape(str(exc))}[/red]",
            )
            return
        self._tracks = tracks
        self.app.call_from_thread(self._populate, name, tracks)

    def _populate(self, name: str, tracks: list[Track]) -> None:
        lst = self.query_one(SelectionList)
        lst.clear_options()
        for i, t in enumerate(tracks):
            lst.add_option(Selection(t.label, i, True))
        self.query_one("#sp-status", Label).update(
            f"[green]{name}[/green]  [dim]({len(tracks)} tracks)[/dim]"
        )
        self.query_one("#sp-go", Button).disabled = False


class SearchPane(Vertical):
    """Search SoundCloud and download a chosen result."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._results: list[dict] = []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="input-row"):
            yield Input(placeholder="Search SoundCloud…", id="se-q")
            yield Button("Search", id="se-btn", variant="primary")
        yield Label("", id="se-status", classes="status-line")
        yield DataTable(id="se-table", zebra_stripes=True, cursor_type="row")
        yield Button(
            "Download highlighted track", id="se-dl", variant="success", disabled=True
        )

    def on_mount(self) -> None:
        tbl = self.query_one(DataTable)
        tbl.add_column("#", width=3, key="n")
        tbl.add_column("Artist", width=24, key="artist")
        tbl.add_column("Title", width=38, key="title")
        tbl.add_column("Dur", width=6, key="dur")

    @on(Button.Pressed, "#se-btn")
    def _on_search_btn(self) -> None:
        self._trigger_search()

    @on(Input.Submitted, "#se-q")
    def _on_query_enter(self) -> None:
        self._trigger_search()

    def _trigger_search(self) -> None:
        q = self.query_one("#se-q", Input).value.strip()
        if q:
            self._do_search(q)

    @on(Button.Pressed, "#se-dl")
    def _on_download(self) -> None:
        tbl = self.query_one(DataTable)
        row_idx = tbl.cursor_row
        if not self._results or not (0 <= row_idx < len(self._results)):
            return
        r = self._results[row_idx]
        url = r.get("webpage_url") or r.get("url") or ""
        title = r.get("title") or "?"
        artist = r.get("uploader") or r.get("channel") or "?"
        self.app.enqueue([Job(display=f"{artist} — {title}", query=url)])  # type: ignore[attr-defined]

    @work(thread=True)
    def _do_search(self, query: str) -> None:
        self.app.call_from_thread(
            self.query_one("#se-status", Label).update,
            "[yellow]Searching…[/yellow]",
        )
        cfg = load_config()
        try:
            results = sc_search(query, cfg, n=8)
        except Exception as exc:
            self.app.call_from_thread(
                self.query_one("#se-status", Label).update,
                f"[red]{exc}[/red]",
            )
            return
        self._results = results
        self.app.call_from_thread(self._show_results, query, results)

    def _show_results(self, query: str, results: list[dict]) -> None:
        tbl = self.query_one(DataTable)
        tbl.clear()
        if not results:
            self.query_one("#se-status", Label).update("[yellow]No results.[/yellow]")
            self.query_one("#se-dl", Button).disabled = True
            return
        for i, r in enumerate(results, 1):
            dur = r.get("duration") or 0
            m, s = divmod(int(dur), 60)
            tbl.add_row(
                str(i),
                (r.get("uploader") or r.get("channel") or "?")[:23],
                (r.get("title") or "?")[:37],
                f"{m}:{s:02d}" if dur else "?",
            )
        self.query_one("#se-status", Label).update(
            f"[dim]{len(results)} results for[/dim] [bold]{query}[/bold]  "
            f"[dim]↑↓ to move, Enter/button to download[/dim]"
        )
        self.query_one("#se-dl", Button).disabled = False


class DirectPane(Vertical):
    """Paste a SoundCloud URL and download."""

    def compose(self) -> ComposeResult:
        yield Label(
            "[dim]Download any SoundCloud track, set, or playlist directly.[/dim]",
            id="di-hint",
        )
        with Horizontal(classes="input-row"):
            yield Input(placeholder="https://soundcloud.com/…", id="di-url")
            yield Button("Download", id="di-go", variant="success")
        yield Label("", id="di-status", classes="status-line")

    @on(Button.Pressed, "#di-go")
    def _on_go(self) -> None:
        self._trigger()

    @on(Input.Submitted, "#di-url")
    def _on_enter(self) -> None:
        self._trigger()

    def _trigger(self) -> None:
        url = self.query_one("#di-url", Input).value.strip()
        if not SOUNDCLOUD_RE.match(url):
            self.query_one("#di-status", Label).update("[red]Not a SoundCloud URL.[/red]")
            return
        self.app.enqueue([Job(display=url, query=url)])  # type: ignore[attr-defined]
        self.query_one("#di-status", Label).update("[green]Added to queue.[/green]")
        self.query_one("#di-url", Input).value = ""


class QueuePane(Vertical):
    """Live download queue with in-place cell updates (no flicker)."""

    STATUS_ICONS: dict[str, str] = {
        "queued":      "⏳ queued",
        "searching":   "🔍 searching",
        "downloading": "↓  loading",
        "done":        "✓  done",
        "failed":      "✗  failed",
    }

    def compose(self) -> ComposeResult:
        yield DataTable(id="q-table", zebra_stripes=True, cursor_type="none")
        yield Label("", id="q-footer", classes="status-line")

    def on_mount(self) -> None:
        tbl = self.query_one(DataTable)
        tbl.add_column("Status", width=15, key="status")
        tbl.add_column("Track", width=44, key="track")
        tbl.add_column("Progress / Error", width=24, key="progress")

    def add_job(self, job: Job) -> None:
        self.query_one(DataTable).add_row(
            self.STATUS_ICONS.get(job.status, job.status),
            job.display[:43],
            "",
            key=job.id,
        )

    def update_job(self, job: Job) -> None:
        tbl = self.query_one(DataTable)
        icon = self.STATUS_ICONS.get(job.status, job.status)
        detail = job.error[:22] if job.status == "failed" else job.progress
        try:
            tbl.update_cell(job.id, "status", icon, update_width=False)
            tbl.update_cell(job.id, "progress", detail, update_width=False)
        except Exception:
            pass

    def update_footer(self, jobs: list[Job]) -> None:
        done = sum(1 for j in jobs if j.status == "done")
        failed = sum(1 for j in jobs if j.status == "failed")
        n = len(jobs)
        txt = f"{done} / {n} complete"
        if failed:
            txt += f"   ·   {failed} failed"
        active = next((j for j in jobs if j.status == "downloading"), None)
        if active and active.progress:
            txt += f"   ·   {active.display[:28]}… {active.progress}"
        self.query_one("#q-footer", Label).update(txt)


class SettingsPane(ScrollableContainer):
    """Persistent configuration."""

    def compose(self) -> ComposeResult:
        cfg = load_config()

        yield Label("[bold]Output directory[/bold]")
        yield Input(
            value=cfg.get("out_dir", str(Path.home() / "Music" / "DJ")),
            id="cfg-out",
        )

        yield Label("")
        yield Label(
            "[bold]Spotify API[/bold]\n"
            "[dim]1. Go to developer.spotify.com → Dashboard → Create App\n"
            "   (Spotify Premium account required to register)\n"
            "2. Add Redirect URI:  http://127.0.0.1:8888/callback\n"
            "   (use 127.0.0.1, not localhost — Spotify's migration guide requires this)\n"
            "3. Copy Client ID + Secret below, Save, then Login.[/dim]"
        )
        yield Label("Client ID")
        yield Input(value=cfg.get("spotify_client_id", ""), id="cfg-sp-id")
        yield Label("Client Secret")
        yield Input(
            value=cfg.get("spotify_client_secret", ""),
            id="cfg-sp-secret",
            password=True,
        )
        yield Label("")
        with Horizontal(classes="btn-row"):
            yield Button("Save", id="cfg-save", variant="primary")
            yield Button("Login with Spotify", id="cfg-sp-login", variant="default")
        yield Label("", id="cfg-sp-status", classes="status-line")

        yield Label("")
        yield Label(
            "[bold]SoundCloud Go+ token[/bold]  [dim](optional — unlocks 256 kbps AAC)[/dim]\n"
            "[dim]DevTools → Network → any SoundCloud request → Authorization header value[/dim]"
        )
        yield Input(value=cfg.get("sc_token", ""), id="cfg-sc-token", password=True)

        yield Label("")
        yield Label("", id="cfg-status", classes="status-line")

    def on_mount(self) -> None:
        self._refresh_login_status()

    def _refresh_login_status(self) -> None:
        cfg = load_config()
        logged_in = spotify_is_logged_in(cfg)
        status = self.query_one("#cfg-sp-status", Label)
        if logged_in:
            status.update("[green]✓ Logged in to Spotify[/green]")
        else:
            status.update("[dim]Not logged in[/dim]")

    @on(Button.Pressed, "#cfg-save")
    def _save(self) -> None:
        save_config({
            "out_dir": self.query_one("#cfg-out", Input).value.strip(),
            "spotify_client_id": self.query_one("#cfg-sp-id", Input).value.strip(),
            "spotify_client_secret": self.query_one("#cfg-sp-secret", Input).value.strip(),
            "sc_token": self.query_one("#cfg-sc-token", Input).value.strip(),
        })
        self.query_one("#cfg-status", Label).update("[green]Saved.[/green]")
        self._refresh_login_status()

    @on(Button.Pressed, "#cfg-sp-login")
    def _login(self) -> None:
        self._do_login()

    @work(thread=True)
    def _do_login(self) -> None:
        self.app.call_from_thread(
            self.query_one("#cfg-sp-status", Label).update,
            "[yellow]Opening browser… waiting for login…[/yellow]",
        )
        cfg = load_config()
        try:
            spotify_login(cfg)
            self.app.call_from_thread(
                self.query_one("#cfg-sp-status", Label).update,
                "[green]✓ Logged in to Spotify[/green]",
            )
        except Exception as exc:
            from rich.markup import escape
            self.app.call_from_thread(
                self.query_one("#cfg-sp-status", Label).update,
                f"[red]Login failed: {escape(str(exc))}[/red]",
            )


class TracklistPane(Vertical):
    """Load a locally-saved 1001tracklists HTML page, pick tracks, download."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tracks: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label(
            "[dim]In your browser: open the set page on 1001tracklists.com → Ctrl+S → save as "
            "HTML. Then load the file here.[/dim]",
            id="tl-hint",
        )
        with Horizontal(classes="input-row"):
            yield Input(placeholder="Path to saved .html file…", id="tl-path")
            yield Button("Browse…", id="tl-browse")
            yield Button("Parse", id="tl-parse", variant="primary", disabled=True)
        yield Label("", id="tl-status", classes="status-line")
        with Horizontal(classes="btn-row"):
            yield Button("Select All", id="tl-all")
            yield Button("None", id="tl-none")
        yield SelectionList(id="tl-list")
        with Horizontal(id="tl-folder-row"):
            yield Label("Download folder: ", id="tl-folder-label")
            yield Input(placeholder="e.g. seven-lions-edc-2026", id="tl-folder")
        yield Checkbox("Number tracks in set order (01 - …, 02 - …)", id="tl-enumerate")
        yield Button("Download Selected", id="tl-go", variant="success", disabled=True)

    @on(Button.Pressed, "#tl-browse")
    def _on_browse(self) -> None:
        self._do_browse()

    @work(thread=True)
    def _do_browse(self) -> None:
        path = pick_html_file()
        if path:
            self.app.call_from_thread(self._set_path, path)

    def _set_path(self, path: str) -> None:
        self.query_one("#tl-path", Input).value = path
        self.query_one("#tl-parse", Button).disabled = False

    @on(Button.Pressed, "#tl-parse")
    def _on_parse_btn(self) -> None:
        path_str = self.query_one("#tl-path", Input).value.strip()
        if path_str:
            self._do_parse(Path(path_str))

    @on(Input.Submitted, "#tl-path")
    def _on_path_enter(self) -> None:
        path_str = self.query_one("#tl-path", Input).value.strip()
        if path_str:
            self.query_one("#tl-parse", Button).disabled = False
            self._do_parse(Path(path_str))

    @work(thread=True)
    def _do_parse(self, path: Path) -> None:
        self.app.call_from_thread(
            self.query_one("#tl-status", Label).update,
            "[yellow]Parsing…[/yellow]",
        )
        try:
            tracks = parse_1001tl_html(path)
        except Exception as exc:
            from rich.markup import escape
            self.app.call_from_thread(
                self.query_one("#tl-status", Label).update,
                f"[red]{escape(str(exc))}[/red]",
            )
            return
        self._tracks = tracks
        self.app.call_from_thread(self._populate, path, tracks)

    def _populate(self, path: Path, tracks: list[dict]) -> None:
        lst = self.query_one(SelectionList)
        lst.clear_options()
        for i, t in enumerate(tracks):
            lst.add_option(Selection(t["display"], i, not t["is_id"]))
        identified = sum(1 for t in tracks if not t["is_id"])
        suffix = (
            f"  [dim]({len(tracks) - identified} unidentified skipped by default)[/dim]"
            if len(tracks) > identified else ""
        )
        self.query_one("#tl-status", Label).update(
            f"[green]{identified} identified tracks[/green]{suffix}"
        )
        self.query_one("#tl-folder", Input).value = path.stem
        self.query_one("#tl-go", Button).disabled = not tracks

    @on(Button.Pressed, "#tl-all")
    def _on_all(self) -> None:
        self.query_one(SelectionList).select_all()

    @on(Button.Pressed, "#tl-none")
    def _on_none(self) -> None:
        self.query_one(SelectionList).deselect_all()

    @on(Button.Pressed, "#tl-go")
    def _on_download(self) -> None:
        lst = self.query_one(SelectionList)
        selected_indices: list[int] = list(lst.selected)
        if not selected_indices:
            self.query_one("#tl-status", Label).update("[yellow]Nothing selected.[/yellow]")
            return
        subfolder = self.query_one("#tl-folder", Input).value.strip()
        enumerate_tracks = self.query_one("#tl-enumerate", Checkbox).value
        width = len(str(len(self._tracks)))  # 2 for ≤99 tracks, 3 for ≤999
        jobs = [
            Job(
                display=self._tracks[i]["display"],
                query=self._tracks[i]["title"],  # already "Artist - Track Name"
                subfolder=subfolder,
                filename_prefix=f"{i + 1:0{width}d} - " if enumerate_tracks else "",
            )
            for i in selected_indices
            if i < len(self._tracks)
        ]
        if jobs:
            self.app.enqueue(jobs)  # type: ignore[attr-defined]


# ─── Main App ────────────────────────────────────────────────────────────────

APP_CSS = """
Screen {
    background: $surface;
}

TabbedContent, ContentSwitcher {
    height: 1fr;
}
TabPane {
    height: 1fr;
    padding: 0;
}
SpotifyPane, SearchPane, DirectPane, QueuePane, TracklistPane {
    height: 1fr;
    padding: 1 2;
}
SettingsPane {
    height: 1fr;
    padding: 1 2;
}

.input-row {
    height: 3;
    margin-bottom: 1;
    align: left middle;
}
.input-row Input {
    width: 1fr;
    margin-right: 1;
}
.input-row Button {
    width: auto;
    min-width: 12;
}

.btn-row {
    height: 3;
    margin-bottom: 1;
}
.btn-row Button {
    margin-right: 1;
    width: auto;
}

.status-line {
    height: 1;
    margin-bottom: 1;
    color: $text-muted;
}

#sp-list {
    height: 1fr;
    border: tall $panel;
    margin-bottom: 1;
}
#sp-go {
    width: 100%;
}

#se-table {
    height: 1fr;
    margin-bottom: 1;
}
#se-dl {
    width: 100%;
}

#q-table {
    height: 1fr;
}

#di-hint {
    margin-bottom: 1;
}

#tl-hint {
    height: auto;
    margin-bottom: 1;
}
#tl-list {
    height: 1fr;
    border: tall $panel;
    margin-bottom: 1;
}
#tl-folder-row {
    height: 3;
    margin-bottom: 1;
    align: left middle;
}
#tl-folder-label {
    width: auto;
    margin-right: 1;
}
#tl-folder-row Input {
    width: 1fr;
}
#tl-enumerate {
    height: 3;
    margin-bottom: 1;
}
#tl-go {
    width: 100%;
}

SettingsPane Label {
    margin-top: 1;
    height: auto;
}
SettingsPane Input {
    margin: 0;
}
#cfg-save {
    margin-top: 2;
    width: 16;
}
"""


class ScdownApp(App):
    TITLE = "scdown"
    SUB_TITLE = "DJ audio downloader"
    CSS = APP_CSS

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("f1", "switch_tab('spotify')", "Spotify"),
        Binding("f2", "switch_tab('search')", "SC Search"),
        Binding("f3", "switch_tab('direct')", "Direct URL"),
        Binding("f4", "switch_tab('queue')", "Queue"),
        Binding("f5", "switch_tab('settings')", "Settings"),
        Binding("f6", "switch_tab('tracklist')", "1001TL"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._jobs: list[Job] = []
        self._processing = False
        self._lock = threading.Lock()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="spotify"):
            with TabPane("Spotify  [dim]F1[/dim]", id="spotify"):
                yield SpotifyPane()
            with TabPane("SC Search  [dim]F2[/dim]", id="search"):
                yield SearchPane()
            with TabPane("Direct URL  [dim]F3[/dim]", id="direct"):
                yield DirectPane()
            with TabPane("Queue  [dim]F4[/dim]", id="queue"):
                yield QueuePane()
            with TabPane("Settings  [dim]F5[/dim]", id="settings"):
                yield SettingsPane()
            with TabPane("1001TL  [dim]F6[/dim]", id="tracklist"):
                yield TracklistPane()
        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id

    # ── Called by panes ──────────────────────────────────────────────────────

    def enqueue(self, jobs: list[Job]) -> None:
        qp = self.query_one(QueuePane)
        for job in jobs:
            self._jobs.append(job)
            qp.add_job(job)
        qp.update_footer(self._jobs)
        self.query_one(TabbedContent).active = "queue"
        with self._lock:
            if not self._processing:
                self._processing = True
                self._run_queue()

    # ── Queue worker (runs in thread pool) ───────────────────────────────────

    @work(thread=True)
    def _run_queue(self) -> None:
        while True:
            # Grab next pending job under lock so no two workers race
            with self._lock:
                pending = [j for j in self._jobs if j.status == "queued"]
                if not pending:
                    self._processing = False
                    return
                job = pending[0]

            cfg = load_config()
            out_dir = Path(cfg.get("out_dir", str(Path.home() / "Music" / "DJ")))
            dest = out_dir / job.subfolder if job.subfolder else out_dir
            dest.mkdir(parents=True, exist_ok=True)

            # ── Step 1: resolve SoundCloud URL via search if needed ──────────
            if not SOUNDCLOUD_RE.match(job.query):
                job.status = "searching"
                self.call_from_thread(self._ui_update, job)
                try:
                    url = sc_resolve_url(job.query, cfg)
                except Exception:
                    url = None
                if not url:
                    job.status = "failed"
                    job.error = "Not found on SoundCloud"
                    self.call_from_thread(self._ui_update, job)
                    continue
                job.query = url  # store resolved URL so we skip search next time

            # ── Step 2: download ─────────────────────────────────────────────
            job.status = "downloading"
            self.call_from_thread(self._ui_update, job)

            def _prog(p: str, _job: Job = job) -> None:
                _job.progress = p
                self.call_from_thread(self._ui_update, _job)

            try:
                sc_download(job.query, cfg, dest, _prog, job.filename_prefix)
                job.status = "done"
                job.progress = ""
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)[:40]
                job.progress = ""
            self.call_from_thread(self._ui_update, job)

    def _ui_update(self, job: Job) -> None:
        qp = self.query_one(QueuePane)
        qp.update_job(job)
        qp.update_footer(self._jobs)


if __name__ == "__main__":
    ScdownApp().run()
