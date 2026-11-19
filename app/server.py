#!/usr/bin/env python3
"""VideoGrab Backend 2.1: Projekte, Quellen, Downloads (yt-dlp + gallery-dl), Umwandlung (ffmpeg)."""
import base64
import datetime
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VERSION = "2.2.0"
UPDATE_URL = "https://raw.githubusercontent.com/Lucran11/videograb/main"
APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR / "ui"
IS_MAC = sys.platform == "darwin"
DATA_DIR = Path.home() / "Library" / "Application Support" / "VideoGrab" if IS_MAC else Path.home() / ".videograb"
TMP_DIR = DATA_DIR / "tmp"
THUMB_DIR = DATA_DIR / "thumbs"
LOG_FILE = DATA_DIR / "videograb.log"
SETTINGS_FILE = DATA_DIR / "settings.json"
SOURCES_FILE = DATA_DIR / "sources.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
QUEUE_FILE = DATA_DIR / "queue.json"
OLD_HISTORY_FILE = DATA_DIR / "history.json"
DEFAULT_BASE = Path.home() / "Downloads" / "VideoGrab"

URL_RE = re.compile(r"https?://[^\s<>\"'\]\)\}]+", re.IGNORECASE)
JUNK = ("schemas.", "w3.org", "purl.org", "openxmlformats", "microsoft.com/office", "apple.com/DTDs",
        "fonts.g", "gstatic", "googletagmanager", "google-analytics", "cdn.")
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".html", ".htm", ".json", ".xml", ".rtf", ".url", ".webloc", ".log"}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".tif", ".tiff", ".bmp")
LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
CODECS = {"prores": "ProRes 422 HQ", "prores_lt": "ProRes 422 LT", "h264": "H.264", "hevc": "HEVC", "original": "Original", "audio": "Nur Ton"}
# Mbit/s bei 1080p25 als Basis fuer die Groessenschaetzung
BITRATE = {"prores": 220, "prores_lt": 102, "h264": 14, "hevc": 9, "audio": 2.4}


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    with LOCK:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    print(line, flush=True)


# --------------------------------------------------------------------------- Persistenz
def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


SETTINGS = {"codec": "prores", "browser": "chrome", "ask_before_start": True, "parallel": 2, "clipboard_watch": True,
            "auto_update": True, "last_update": 0, "notify": True, "gdoc_url": "", "gdoc_token": "", "project": "",
            "warn_gb": 20, "update_url": UPDATE_URL, "last_app_check": 0}
SETTINGS.update(load_json(SETTINGS_FILE, {}))
PROJECTS = load_json(PROJECTS_FILE, [])
SOURCES = load_json(SOURCES_FILE, [])


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def ensure_default_project():
    global PROJECTS
    if not PROJECTS:
        PROJECTS = [{"id": "default", "name": "Allgemein", "folder": str(DEFAULT_BASE), "gdoc_url": "", "gdoc_id": "", "created": now_iso()}]
        save_json(PROJECTS_FILE, PROJECTS)
    if not any(p["id"] == SETTINGS.get("project") for p in PROJECTS):
        SETTINGS["project"] = PROJECTS[0]["id"]


def migrate_history():
    """Altes Archiv (history.json) einmalig in die Quellenliste uebernehmen."""
    global SOURCES
    if OLD_HISTORY_FILE.exists() and not SOURCES_FILE.exists():
        for h in load_json(OLD_HISTORY_FILE, []):
            h.setdefault("project", "default")
            h.setdefault("kind", "video")
            h.setdefault("note", "")
            SOURCES.append(h)
        save_json(SOURCES_FILE, SOURCES)
        try:
            OLD_HISTORY_FILE.rename(OLD_HISTORY_FILE.with_suffix(".json.alt"))
        except Exception:
            pass


def project(pid=None):
    pid = pid or SETTINGS.get("project")
    return next((p for p in PROJECTS if p["id"] == pid), PROJECTS[0] if PROJECTS else None)


# --------------------------------------------------------------------------- Plattformen
PLATFORMS = [
    ("youtube", ("youtube.com", "youtu.be", "youtube-nocookie.com"), "YouTube"),
    ("instagram", ("instagram.com",), "Instagram"),
    ("tiktok", ("tiktok.com",), "TikTok"),
    ("ard", ("ardmediathek.de", "daserste.de", "tagesschau.de", "sportschau.de", "wdr.de", "ndr.de", "br.de", "swr.de", "mdr.de", "hr.de", "rbb-online.de", "ardaudiothek.de"), "ARD"),
    ("zdf", ("zdf.de", "zdfheute.de", "3sat.de", "kika.de", "phoenix.de"), "ZDF"),
    ("arte", ("arte.tv",), "Arte"),
    ("vimeo", ("vimeo.com",), "Vimeo"),
    ("x", ("x.com", "twitter.com", "t.co"), "X"),
    ("facebook", ("facebook.com", "fb.watch", "fb.com"), "Facebook"),
    ("twitch", ("twitch.tv",), "Twitch"),
    ("dailymotion", ("dailymotion.com", "dai.ly"), "Dailymotion"),
    ("reddit", ("reddit.com", "redd.it"), "Reddit"),
    ("linkedin", ("linkedin.com",), "LinkedIn"),
    ("soundcloud", ("soundcloud.com",), "SoundCloud"),
    ("bilibili", ("bilibili.com",), "Bilibili"),
    ("pinterest", ("pinterest.com", "pinterest.de", "pinterest.at", "pinterest.ch", "pinterest.co.uk", "pin.it"), "Pinterest"),
    ("snapchat", ("snapchat.com",), "Snapchat"),
    ("drm", ("netflix.com", "disneyplus.com", "primevideo.com", "amazon.de/gp/video", "wow.tv", "sky.de", "joyn.de", "rtlplus.de", "rtl.de", "dazn.com", "paramountplus.com", "appletv", "tv.apple.com", "spotify.com"), "DRM"),
]


def platform_of(url):
    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path.lower()
    for key, hosts, name in PLATFORMS:
        for h in hosts:
            if "/" in h:
                if (host + path).startswith(h) or ("." + host + path).find("." + h) >= 0:
                    return key, name
            elif host == h or host.endswith("." + h):
                return key, name
    if path.endswith(IMAGE_EXT):
        return "image", "Bild"
    if re.search(r"\.(mp4|mov|m4v|webm|mkv|avi|mp3|wav|m4a|m3u8)(\?|$)", path):
        return "file", "Direktlink"
    return "web", host.replace("www.", "") or "Web"


# --------------------------------------------------------------------------- Links aus Text/Dateien
def clean_url(u):
    u = html.unescape(u.strip()).rstrip(".,;:!?)")
    u = re.sub(r"[?&](utm_[a-z]+|igsh|igshid|feature|si|fbclid|ref_src|ref_url|s|t)=[^&#]*", "", u)
    u = re.sub(r"\?&", "?", u).rstrip("?&")
    return u


def urls_from_text(text):
    out = []
    for m in URL_RE.findall(text or ""):
        u = clean_url(m)
        if any(j in u.lower() for j in JUNK):
            continue
        if u not in out:
            out.append(u)
    return out


def text_from_bytes(name, data):
    ext = Path(name).suffix.lower()
    try:
        if ext == ".pdf":
            if shutil.which("pdftotext"):
                r = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True)
                return r.stdout.decode("utf-8", "ignore")
            try:
                import io
                import pypdf  # type: ignore
                return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(data)).pages)
            except ImportError:
                return ""
        if ext in TEXT_EXT or not data[:4] == b"PK\x03\x04":
            return data.decode("utf-8", "ignore")
        import io
        text = ""
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.endswith((".xml", ".txt", ".html", ".json", ".iwa")) or "/" not in n:
                    try:
                        text += z.read(n).decode("utf-8", "ignore") + "\n"
                    except Exception:
                        pass
        return html.unescape(text)
    except Exception as e:
        log(f"Datei {name} nicht lesbar: {e}")
        return ""


# --------------------------------------------------------------------------- Fehlertexte
ERROR_MAP = [
    (r"sign in to confirm|not a bot", "YouTube verlangt Login (Bot-Sperre). In Chrome bei YouTube eingeloggt sein, dann klappt es mit Cookies."),
    (r"login required|rate-limit|only works when logged-in|log in|logged-in|cookies|authentication", "Seite verlangt Login. Im Browser auf der Seite eingeloggt sein (Cookies werden automatisch genutzt)."),
    (r"private video|this video is private|is private", "Video ist privat."),
    (r"unavailable|not available|removed|deleted|does not exist|404|no video formats|gone", "Inhalt nicht (mehr) verfuegbar oder geloescht."),
    (r"geo|not available in your country|blocked in your", "Geoblocking: in deinem Land gesperrt (VPN noetig)."),
    (r"\bage[- ]?(restricted|gated|verification)|confirm your age|inappropriate for some users", "Altersbeschraenkt. Im Browser eingeloggt sein, dann nochmal."),
    (r"drm|widevine|fairplay|protected", "DRM-geschuetzt (Streamingdienst). Download technisch nicht moeglich."),
    (r"unsupported url|no video could be found|unable to extract|no media found|no results", "Auf dieser Seite wurde kein Video oder Bild gefunden. Direkt den Link zum Beitrag (nicht zur Uebersicht) nehmen."),
    (r"is live|live stream|livestream|premieres? in", "Livestream oder Premiere: erst nach Ende als Aufzeichnung ladbar."),
    (r"http error 429|too many requests|rate.?limit", "Zu viele Anfragen, Seite bremst. Kurz warten und nochmal."),
    (r"http error 403|forbidden", "Zugriff verweigert (403). Meist hilft Login im Browser oder ein Update."),
    (r"timed out|connection|network|name or service|resolve", "Netzwerkproblem. Internet pruefen und nochmal versuchen."),
    (r"no space left", "Festplatte voll."),
    (r"playlist", "Das ist eine Playlist/Kanalseite. Bitte Links zu einzelnen Beitraegen nehmen."),
]
COOKIE_FAIL = re.compile(r"could not (find|copy|read).*cookie|cookies database|failed to decrypt|keychain|safe storage|no cookies", re.I)


def friendly_error(raw):
    hint = ""
    m = re.search(r"COOKIEFAIL (\w+)", raw or "")
    if m:
        hint = f" Cookies aus {m.group(1).capitalize()} konnten nicht gelesen werden: Browser installiert und Schluesselbund-Zugriff erlaubt? (Einstellungen > Browser)"
        raw = raw.replace(m.group(0), "")
    low = (raw or "").lower()
    for pat, msg in ERROR_MAP:
        if re.search(pat, low):
            return msg + hint
    short = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", (raw or "").strip().splitlines()[-1] if raw and raw.strip() else "")
    return (short[:160] or "Unbekannter Fehler") + hint + "  (Tipp: Einstellungen > yt-dlp aktualisieren)"


def needs_cookies(err):
    low = (err or "").lower()
    return any(k in low for k in ("sign in", "login", "logged-in", "cookies", "not a bot", "rate-limit", "age-restricted",
                                  "age restricted", "confirm your age", "403", "private", "authentication", "401"))


def merge_errors(first, second, browser):
    if second and COOKIE_FAIL.search(second):
        return (first or "") + f"\nCOOKIEFAIL {browser}"
    return second or first


# --------------------------------------------------------------------------- Warteschlange
QUEUE = []
PROCS = {}
WORKERS = []
RUNNING = False
INFO_SEM = threading.Semaphore(4)
NATIVE = {"choose_folder": None, "notify": None, "badge": None}


def new_item(url, pid=None):
    key, name = platform_of(url)
    return {"id": uuid.uuid4().hex[:10], "url": url, "platform": key, "platform_name": name, "project": pid or SETTINGS.get("project"),
            "title": "", "duration": None, "thumb": "", "status": "info", "progress": 0.0, "speed": "", "eta": "",
            "error": "", "output": "", "size": 0, "stage": "", "added": time.time(), "codec": SETTINGS.get("codec", "prores"),
            "start": None, "end": None, "kind": "video", "width": 0, "height": 0, "fps": 0, "filesize": 0, "uploader": "", "note": ""}


def set_item(item, **kw):
    with STATE_LOCK:
        item.update(kw)


def persist_queue():
    with STATE_LOCK:
        keep = [dict(i) for i in QUEUE if i["status"] not in ("done", "removed")]
    for i in keep:
        if i["status"] in ("downloading", "converting", "waiting", "cancelled"):
            i.update(status="ready" if i.get("title") else "info", progress=0.0, speed="", eta="")
        i.pop("entries", None)
    try:
        save_json(QUEUE_FILE, keep)
    except Exception as e:
        log(f"Queue nicht gespeichert: {e}")


def restore_queue():
    for i in load_json(QUEUE_FILE, []):
        try:
            it = new_item(i["url"], i.get("project"))
            it.update({k: v for k, v in i.items() if k in it})
            if it["status"] not in ("ready", "error", "playlist"):
                it["status"] = "info"
            if it["status"] == "playlist":
                it["status"] = "info"
            QUEUE.append(it)
        except Exception:
            pass
    for it in QUEUE:
        if it["status"] == "info":
            threading.Thread(target=fetch_info, args=(it,), daemon=True).start()


def ytdlp_base(cookies):
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist", "--socket-timeout", "30"]
    if cookies and cookies != "none":
        cmd += ["--cookies-from-browser", cookies]
    return cmd


def run_ytdlp(args, timeout=120):
    browser = SETTINGS.get("browser", "chrome")
    attempts = [None] + ([browser] if browser != "none" else [])
    res, first = None, ""
    for c in attempts:
        res = subprocess.run(ytdlp_base(c) + args, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0:
            return res
        if c is None:
            first = res.stderr
            if not needs_cookies(first):
                break
        else:
            res.stderr = merge_errors(first, res.stderr, c)
    return res


def gallery_base(cookies):
    cmd = ["gallery-dl", "-q"]
    if cookies and cookies != "none":
        cmd += ["--cookies-from-browser", cookies]
    return cmd


def run_gallery(args, timeout=120):
    if not shutil.which("gallery-dl"):
        return None
    browser = SETTINGS.get("browser", "chrome")
    attempts = [None] + ([browser] if browser != "none" else [])
    res, first = None, ""
    for c in attempts:
        res = subprocess.run(gallery_base(c) + args, capture_output=True, text=True, timeout=timeout)
        if res.returncode == 0 and res.stdout.strip():
            return res
        if c is None:
            first = res.stderr
            if not needs_cookies(first) and "instagram" not in " ".join(args).lower() and "x.com" not in " ".join(args).lower():
                break
        else:
            res.stderr = merge_errors(first, res.stderr, c)
    return res


def cache_thumb(item_id, url=None, data=None):
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / f"{item_id}.jpg"
    try:
        if data is None:
            if not url:
                return ""
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/605 Safari/605"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(8_000_000)
        if len(data) < 100:
            return ""
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-vf", "scale=320:-2",
                            "-q:v", "4", "-f", "image2", str(dest)], input=data, capture_output=True, timeout=30)
        if r.returncode != 0 or not dest.exists():
            return ""
        return f"/thumb/{item_id}.jpg?v={int(dest.stat().st_mtime)}"
    except Exception as e:
        log(f"Thumbnail nicht ladbar: {e}")
        return ""


def fetch_info(item):
    with INFO_SEM:
        if item.get("status") != "info":
            return
        try:
            _fetch_info(item)
        except Exception as e:
            set_item(item, status="error", error=f"Abfrage fehlgeschlagen: {str(e)[-150:]}")
        persist_queue()


def _fetch_info(item):
    if item["platform"] == "drm":
        set_item(item, status="error", error="DRM-geschuetzter Streamingdienst. Download technisch nicht moeglich.")
        return
    if item["platform"] == "image":
        _fetch_image_info(item)
        return
    try:
        res = run_ytdlp(["--flat-playlist", "-J", item["url"]], timeout=90)
    except subprocess.TimeoutExpired:
        set_item(item, status="error", error="Zeitueberschreitung beim Abfragen der Seite.")
        return
    if res.returncode != 0:
        # Kein Video? Vielleicht ein Bild (Instagram-Foto, Pinterest, X-Bild ...)
        if _fetch_image_info(item, quiet=True):
            return
        set_item(item, status="error", error=friendly_error(res.stderr))
        log(f"INFO-Fehler {item['url']}: {res.stderr.strip()[-300:]}")
        return
    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError:
        set_item(item, status="error", error="Seite liefert keine Videodaten.")
        return
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e and e.get("url")]
        if not entries:
            if _fetch_image_info(item, quiet=True):
                return
            set_item(item, status="error", error="Auf dieser Seite wurde kein Video gefunden.")
            return
        if len(entries) == 1:
            with STATE_LOCK:
                item["url"] = entries[0]["url"]
                item["platform"], item["platform_name"] = platform_of(item["url"])
            _fetch_info(item)
            return
        set_item(item, status="playlist", title=info.get("title") or "Playlist",
                 entries=[{"url": e["url"], "title": e.get("title") or ""} for e in entries[:200]],
                 error=f"{len(entries)} Beitraege auf dieser Seite" + (" (nur die ersten 200 werden uebernommen)" if len(entries) > 200 else ""))
        return
    vid = f"{info.get('extractor_key')}:{info.get('id')}"
    with STATE_LOCK:
        dup = next((i for i in QUEUE if i is not item and i.get("vid") == vid), None)
        item["vid"] = vid
    if dup:
        set_item(item, status="error", error="Gleicher Beitrag ist schon in der Liste.", title=info.get("title") or "")
        return
    thumb = info.get("thumbnail") or ""
    if not thumb and info.get("thumbnails"):
        thumb = info["thumbnails"][-1].get("url", "")
    thumb = cache_thumb(item["id"], thumb)
    fps = info.get("fps") or 0
    try:
        fps = float(fps)
    except Exception:
        fps = 0
    set_item(item, title=info.get("title") or info.get("id") or item["url"], duration=info.get("duration"),
             thumb=thumb, status="ready", uploader=info.get("uploader") or info.get("channel") or "",
             width=info.get("width") or 0, height=info.get("height") or 0, fps=fps,
             filesize=info.get("filesize") or info.get("filesize_approx") or 0, kind="audio" if info.get("vcodec") in ("none", None) and info.get("acodec") not in ("none", None) and not info.get("width") else "video")
    if info.get("is_live"):
        set_item(item, status="error", error="Livestream laeuft noch. Erst nach Ende als Aufzeichnung ladbar.")


def _fetch_image_info(item, quiet=False):
    """Bilder ueber gallery-dl (oder Direktlink) erkennen."""
    url = item["url"]
    if item["platform"] == "image":
        set_item(item, kind="image", images=[url], title=Path(urlparse(url).path).stem, status="ready", thumb=cache_thumb(item["id"], url) or "")
        return True
    try:
        res = run_gallery(["-j", url], timeout=90)
    except subprocess.TimeoutExpired:
        res = None
    if res is None:
        if not quiet:
            set_item(item, status="error", error="gallery-dl fehlt (fuer Bilder). Bitte Installation erneut ausfuehren.")
        return False
    if res.returncode != 0 or not res.stdout.strip():
        if not quiet:
            set_item(item, status="error", error=friendly_error(res.stderr))
        return False
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return False
    files = [x for x in data if isinstance(x, list) and x and x[0] == 3]
    imgs = [x for x in files if str(x[2].get("extension", "")).lower() in ("jpg", "jpeg", "png", "webp", "gif", "avif", "heic")]
    if not imgs:
        return False
    meta = imgs[0][2]
    title = meta.get("description") or meta.get("title") or meta.get("content") or meta.get("alt") or f"{item['platform_name']} Bild"
    title = re.sub(r"\s+", " ", str(title)).strip()[:90]
    author = meta.get("author")
    uploader = meta.get("username") or meta.get("owner") or (author.get("name") if isinstance(author, dict) else author) or ""
    set_item(item, kind="image", images=[x[1] for x in imgs], title=title, uploader=str(uploader or ""), status="ready",
             duration=None, thumb=cache_thumb(item["id"], imgs[0][1]) or "", error="")
    with STATE_LOCK:
        item["images_count"] = len(imgs)
    return True


def estimate_bytes(item):
    """Grobe Groessenschaetzung in Bytes fuer die Warnung vor dem Start."""
    codec = item.get("codec") or "prores"
    if item.get("kind") == "image":
        return 3_000_000 * max(1, item.get("images_count", 1))
    dur = item.get("duration") or 0
    if item.get("start") is not None or item.get("end") is not None:
        s, e = item.get("start") or 0, item.get("end") or dur
        dur = max(0, (e or dur) - s)
    if codec == "original":
        return int(item.get("filesize") or (dur * 1.0e6))
    if item.get("kind") == "audio" or codec == "audio":
        return int(dur * BITRATE["audio"] * 1e6 / 8)
    px = (item.get("width") or 1920) * (item.get("height") or 1080)
    fps = item.get("fps") or 25
    scale = (px / (1920 * 1080)) * (min(fps, 60) / 25)
    return int(dur * BITRATE[codec] * scale * 1e6 / 8)


def safe_name(s):
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]", " ", s or "").strip()
    s = re.sub(r"\s+", " ", s).replace("..", ".").strip(". ")
    return s[:90] or "download"


def unique_path(folder, stem, ext):
    p = folder / f"{stem}{ext}"
    i = 2
    while p.exists():
        p = folder / f"{stem} ({i}){ext}"
        i += 1
    return p


def de_date(iso):
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return (iso or "")[:16].replace("T", " ")


def short_time(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m{sec % 60:02d}s" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


def hms(sec):
    sec = int(sec or 0)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def add_source(item, final, size, codec):
    rec = {"id": item["id"], "project": item.get("project") or SETTINGS.get("project"), "url": item["url"], "title": item.get("title"),
           "platform": item["platform"], "platform_name": item["platform_name"], "output": str(final), "size": size,
           "codec": codec, "date": now_iso(), "thumb": item.get("thumb", ""), "duration": item.get("duration"),
           "uploader": item.get("uploader", ""), "kind": item.get("kind", "video"), "note": item.get("note", ""),
           "start": item.get("start"), "end": item.get("end")}
    with STATE_LOCK:
        SOURCES.insert(0, rec)
        save_json(SOURCES_FILE, SOURCES[:5000])


def download_item(item):
    proj = project(item.get("project")) or project()
    out_dir = Path(proj["folder"]).expanduser()
    codec = item.get("codec") or SETTINGS.get("codec", "prores")
    work = TMP_DIR / item["id"]
    work.mkdir(parents=True, exist_ok=True)
    set_item(item, status="downloading", stage="Download", progress=0.0, error="")
    try:
        if item.get("kind") == "image":
            return download_images(item, work, out_dir)
        return download_video(item, work, out_dir, codec)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def download_images(item, work, out_dir):
    urls = item.get("images") or []
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(item.get("title") or "bild")
    finals = []
    if item["platform"] == "image" or not shutil.which("gallery-dl"):
        import urllib.request
        for n, u in enumerate(urls, 1):
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/605 Safari/605"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            ext = Path(urlparse(u).path).suffix.lower() or ".jpg"
            (work / f"{n:02d}{ext}").write_bytes(data)
            set_item(item, progress=0.6 * n / max(1, len(urls)))
    else:
        browser = SETTINGS.get("browser", "chrome")
        attempts = [None] + ([browser] if browser != "none" else [])
        ok, err = False, ""
        for c in attempts:
            proc = subprocess.run(gallery_base(c) + ["-D", str(work), item["url"]], capture_output=True, text=True, timeout=600)
            if proc.returncode == 0 and any(work.iterdir()):
                ok = True
                break
            err = merge_errors(err, proc.stderr, c) if c else proc.stderr
        if not ok:
            set_item(item, status="error", error=friendly_error(err))
            log(f"BILD-Fehler {item['url']}: {err.strip()[-300:]}")
            return
    files = sorted(p for p in work.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT)
    if not files:
        set_item(item, status="error", error="Keine Bilddatei erhalten.")
        return
    set_item(item, status="converting", stage="Umwandeln", progress=0.6)
    total = 0
    for n, f in enumerate(files, 1):
        ext = f.suffix.lower()
        suffix = f" {n:02d}" if len(files) > 1 else ""
        if ext in (".jpg", ".jpeg", ".png", ".gif"):
            final = unique_path(out_dir, stem + suffix, ".jpg" if ext == ".jpeg" else ext)
            shutil.move(str(f), final)
        else:  # webp, avif, heic ... -> png (Premiere-tauglich)
            final = unique_path(out_dir, stem + suffix, ".png")
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-frames:v", "1", str(final)], capture_output=True, text=True)
            if r.returncode != 0:
                set_item(item, status="error", error="Bild konnte nicht umgewandelt werden: " + r.stderr[-150:])
                return
        finals.append(final)
        total += final.stat().st_size
        set_item(item, progress=0.6 + 0.4 * n / len(files))
    first = finals[0]
    set_item(item, status="done", progress=1.0, output=str(first), size=total, stage="Fertig",
             error="" if len(finals) == 1 else f"{len(finals)} Bilder")
    add_source(item, first, total, "image")
    log(f"OK {len(finals)} Bild(er) -> {out_dir}")


def download_video(item, work, out_dir, codec):
    audio_only = codec == "audio" or item.get("kind") == "audio"
    cmd_tail = ["--newline", "--progress", "--progress-template",
                "download:VGP %(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(progress.fragment_index)s|%(progress.fragment_count)s",
                "--retries", "5", "--fragment-retries", "10", "-N", "4", "--continue",
                "-o", str(work / "src.%(ext)s"), "--print", "after_move:filepath", "--no-simulate"]
    has_range = item.get("start") is not None or item.get("end") is not None
    if has_range and not item.get("cut_in_convert"):
        s = hms(item.get("start") or 0)
        e = hms(item["end"]) if item.get("end") else "inf"
        cmd_tail += ["--download-sections", f"*{s}-{e}", "--force-keyframes-at-cuts"]
    if audio_only:
        cmd_tail += ["-f", "ba/b"]
    elif codec == "original":
        # Hoechste Qualitaet, die die Seite hat (bei YouTube 4K/8K als VP9/AV1), danach nur neu verpackt
        cmd_tail += ["-f", "bv*+ba/b", "--merge-output-format", "mkv"]
    else:
        cmd_tail += ["-f", "bv*+ba/b", "--merge-output-format", "mkv"]

    browser = SETTINGS.get("browser", "chrome")
    attempts = [None] + ([browser] if browser != "none" else [])
    src, err = None, ""
    for c in attempts:
        cmd = ytdlp_base(c) + cmd_tail + [item["url"]]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        PROCS[item["id"]] = proc
        lines = []
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("VGP "):
                try:
                    pct, speed, eta, fi, fc = line[4:].split("|")
                    frac = float(pct.strip().rstrip("%") or 0) / 100
                    if fc.strip().isdigit() and int(fc) > 0 and fi.strip().isdigit():
                        frac = max(frac, int(fi) / int(fc))
                    scale = 1.0 if codec == "original" else 0.6
                    set_item(item, progress=min(frac, 1.0) * scale, speed=speed.strip().replace("Unknown", ""),
                             eta=eta.strip().replace("Unknown", ""))
                except ValueError:
                    pass
            elif line:
                lines.append(line)
        proc.wait()
        PROCS.pop(item["id"], None)
        if item.get("status") == "cancelled":
            return
        if proc.returncode == 0:
            cands = [Path(l) for l in lines if Path(l).exists()] or sorted(work.glob("src.*"))
            cands = [p for p in cands if not p.name.endswith((".part", ".ytdl"))]
            if cands:
                src = cands[-1]
                break
        e2 = proc.stderr.read()
        if c is None:
            err = e2
            if not needs_cookies(err):
                break
        else:
            err = merge_errors(err, e2, c)
    if src is None and has_range and not item.get("cut_in_convert"):
        # Ausschnitt direkt beim Laden ging nicht (manche Sender blocken das): ganz laden, beim Umwandeln schneiden
        log(f"Ausschnitt-Download fehlgeschlagen, lade komplett und schneide danach: {err.strip()[-120:]}")
        set_item(item, cut_in_convert=True, progress=0.0)
        return download_video(item, work, out_dir, codec)
    if src is None:
        set_item(item, status="error", error=friendly_error(err), speed="", eta="")
        log(f"DOWNLOAD-Fehler {item['url']}: {err.strip()[-400:]}")
        return

    stem = safe_name(item.get("title") or src.stem)
    if item.get("start") is not None or item.get("end") is not None:
        stem += f" [{short_time(item.get('start') or 0)}-{short_time(item['end']) if item.get('end') else 'Ende'}]"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if codec == "original" and item.get("cut_in_convert") and has_range:
            cut = src.with_name("cut" + src.suffix)
            r = subprocess.run(["ffmpeg", "-y", "-v", "error"] + cut_args(item) + ["-i", str(src), "-c", "copy", str(cut)], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr[-200:])
            final = unique_path(out_dir, stem, src.suffix)
            shutil.move(str(cut), final)
        elif codec == "original":
            final = remux_original(src, out_dir, stem)
        else:
            set_item(item, status="converting", stage="Umwandeln", speed="", eta="", progress=0.6 if not audio_only else 0.9)
            tmp_out = convert(item, src, "audio" if audio_only else codec)
            final = unique_path(out_dir, stem, tmp_out.suffix)
            shutil.move(str(tmp_out), final)
    except Exception as e:
        set_item(item, status="error", error=f"Umwandlung fehlgeschlagen: {str(e)[-200:]}")
        log(f"CONVERT-Fehler {item['url']}: {e}")
        return
    size = final.stat().st_size if final.exists() else 0
    set_item(item, status="done", progress=1.0, output=str(final), size=size, stage="Fertig", speed="", eta="")
    add_source(item, final, size, codec)
    log(f"OK {final}")


def remux_original(src, out_dir, stem):
    """Original unveraendert lassen, nur in .mp4 (oder .mov) umverpacken, damit Premiere es oeffnen kann."""
    for ext in (".mp4", ".mov"):
        tmp = src.with_name("remux" + ext)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                            "-movflags", "+faststart", str(tmp)], capture_output=True, text=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            final = unique_path(out_dir, stem, ext)
            shutil.move(str(tmp), final)
            return final
        tmp.unlink(missing_ok=True)
    final = unique_path(out_dir, stem, src.suffix)   # Rueckfall: so lassen wie geladen
    shutil.move(str(src), final)
    return final


def cut_args(item):
    """ffmpeg-Eingabeoptionen fuer einen Ausschnitt (wenn nicht schon beim Laden geschnitten wurde)."""
    if not item.get("cut_in_convert"):
        return []
    args = []
    if item.get("start"):
        args += ["-ss", str(float(item["start"]))]
    if item.get("end"):
        args += ["-t", str(float(item["end"]) - float(item.get("start") or 0))]
    return args


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def fps_of(v):
    r = v.get("avg_frame_rate") or v.get("r_frame_rate") or "25/1"
    try:
        n, d = r.split("/")
        f = float(n) / float(d)
        return f if 1 < f < 240 else 25.0
    except Exception:
        return 25.0


def convert(item, src, codec):
    info = probe(src)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or item.get("duration") or 0)
    if item.get("cut_in_convert") and (item.get("start") or item.get("end")):
        duration = max(0.0, float(item.get("end") or duration) - float(item.get("start") or 0))
    color = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]

    if codec == "audio" or v is None:
        if a is None:
            raise RuntimeError("keine Tonspur gefunden")
        ext = ".wav"
        cmd = ["-vn", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "2"]
    else:
        fps = fps_of(v)
        audio = ["-c:a", "pcm_s24le", "-ar", "48000", "-ac", "2"] if a else ["-an"]
        if codec == "prores":
            ext = ".mov"
            video = ["-vf", f"fps={fps:.6g},format=yuv422p10le", "-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-bits_per_mb", "8000"] + color
        elif codec == "prores_lt":
            ext = ".mov"
            video = ["-vf", f"fps={fps:.6g},format=yuv422p10le", "-c:v", "prores_ks", "-profile:v", "1", "-vendor", "apl0"] + color
        elif codec == "h264":
            ext = ".mp4"
            video = ["-vf", f"fps={fps:.6g},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-g", str(int(round(fps))), "-movflags", "+faststart"] + color
            audio = ["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2"] if a else ["-an"]
        elif codec == "hevc":
            ext = ".mp4"
            enc = ["-c:v", "hevc_videotoolbox", "-q:v", "70"] if IS_MAC else ["-c:v", "libx265", "-crf", "18"]
            video = ["-vf", f"fps={fps:.6g},format=yuv420p"] + enc + ["-tag:v", "hvc1", "-g", str(int(round(fps))), "-movflags", "+faststart"] + color
            audio = ["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2"] if a else ["-an"]
        else:
            raise ValueError(codec)
        cmd = ["-map", "0:v:0"] + (["-map", "0:a:0?"] if a else []) + video + audio + ["-fps_mode", "cfr"]

    tmp = src.with_name("out" + ext)
    variants = [cmd]
    if IS_MAC and codec in ("prores", "prores_lt") and v is not None:
        hw = [c for c in cmd]
        hw[hw.index("prores_ks")] = "prores_videotoolbox"
        hw = [c for c in hw if c not in ("-vendor", "apl0", "-bits_per_mb", "8000")]
        hw[hw.index("-vf") + 1] = f"fps={fps_of(v):.6g}"
        hw[hw.index("-profile:v") + 1] = {"3": "hq", "1": "lt"}[hw[hw.index("-profile:v") + 1]]
        variants.insert(0, hw)
    err = ""
    for cmd_v in variants:
        full = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-progress", "pipe:1"] + cut_args(item) + ["-i", str(src)] + cmd_v + ["-map_metadata", "-1", str(tmp)]
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        PROCS[item["id"]] = proc
        for line in proc.stdout:
            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    t = int(line.split("=")[1]) / 1_000_000
                    base = 0.6 if codec != "audio" else 0.9
                    set_item(item, progress=min(0.99, base + (1 - base) * t / duration))
                except ValueError:
                    pass
        proc.wait()
        PROCS.pop(item["id"], None)
        if item.get("status") == "cancelled":
            tmp.unlink(missing_ok=True)
            raise RuntimeError("abgebrochen")
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            return tmp
        err = proc.stderr.read().strip()[-300:]
        log(f"ffmpeg-Variante fehlgeschlagen: {err}")
        tmp.unlink(missing_ok=True)
    raise RuntimeError(err)


def worker_loop():
    global RUNNING
    while True:
        with STATE_LOCK:
            item = next((i for i in QUEUE if i["status"] == "ready"), None)
            if item:
                item["status"] = "waiting"
        if not item:
            break
        try:
            download_item(item)
        except Exception as e:
            set_item(item, status="error", error=str(e)[-200:])
            log(f"Worker-Fehler: {e}")
        persist_queue()
        if item["platform"] == "instagram":
            time.sleep(4)   # Instagram-Drossel: Account nicht in die Sperre laufen lassen
    with STATE_LOCK:
        WORKERS[:] = [w for w in WORKERS if w.is_alive() and w is not threading.current_thread()]
        last = not WORKERS
        if last:
            RUNNING = False
    if last:
        done = [i for i in QUEUE if i["status"] == "done"]
        failed = [i for i in QUEUE if i["status"] == "error" and i.get("title")]
        if NATIVE.get("notify") and SETTINGS.get("notify", True) and (done or failed):
            msg = f"{len(done)} fertig" + (f", {len(failed)} fehlgeschlagen" if failed else "")
            try:
                NATIVE["notify"]("VideoGrab", msg)
            except Exception:
                pass
        update_badge()


def update_badge():
    if NATIVE.get("badge"):
        n = sum(1 for i in QUEUE if i["status"] in ("downloading", "converting", "waiting"))
        try:
            NATIVE["badge"](str(n) if n else "")
        except Exception:
            pass


def start_downloads():
    global RUNNING
    RUNNING = True
    n = max(1, min(4, int(SETTINGS.get("parallel", 2))))
    for _ in range(n):
        t = threading.Thread(target=worker_loop, daemon=True)
        WORKERS.append(t)
        t.start()


# --------------------------------------------------------------------------- Quellen-Export
def sources_for(pid, ids=None):
    with STATE_LOCK:
        return [dict(s) for s in SOURCES if (pid == "all" or s.get("project") == pid) and (not ids or s["id"] in ids)]


def sources_markdown(proj, rows):
    stamp = datetime.datetime.now()
    name = "Alle Projekte" if proj is None else proj["name"]
    lines = [f"# Quellen: {name}", "", f"Stand {stamp:%d.%m.%Y, %H:%M} (VideoGrab)", ""]
    for n, r in enumerate(rows, 1):
        lines.append(f"{n}. **{(r.get('title') or r.get('url') or '').strip()}**")
        meta = f"   {r.get('platform_name') or ''}" + (f" · {r['uploader']}" if r.get("uploader") else "") + (" · Bild" if r.get("kind") == "image" else "")
        lines.append(meta)
        lines.append(f"   {r.get('url')}")
        if r.get("note"):
            lines.append(f"   Notiz: {r['note']}")
        if r.get("date"):
            lines.append(f"   Geladen: {de_date(r['date'])}" + (f" · Datei: {Path(r['output']).name}" if r.get("output") else ""))
        lines.append("")
    lines += ["", "## Nur die Links", ""] + [r.get("url") for r in rows] + [""]
    return "\n".join(lines)


def export_markdown(pid, ids=None):
    proj = None if pid == "all" else project(pid)
    rows = sources_for(pid, ids)
    if not rows:
        return {"ok": False, "error": "Keine Quellen zum Exportieren"}
    out_dir = Path(proj["folder"] if proj else DEFAULT_BASE).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(out_dir, f"Quellen {proj['name'] if proj else 'alle'} {datetime.datetime.now():%Y-%m-%d}", ".md")
    dest.write_text(sources_markdown(proj, rows), "utf-8")
    reveal(str(dest))
    return {"ok": True, "path": str(dest), "count": len(rows)}


def export_gdoc(pid, ids=None):
    url = (SETTINGS.get("gdoc_url") or "").strip()
    if not url.startswith("https://script.google.com/"):
        return {"ok": False, "error": "Keine Google-Docs-Verbindung eingerichtet (Einstellungen > Google Docs)."}
    proj = None if pid == "all" else project(pid)
    rows = sources_for(pid, ids)
    if not rows:
        return {"ok": False, "error": "Keine Quellen zum Exportieren"}
    payload = {"token": SETTINGS.get("gdoc_token", ""), "project": proj["name"] if proj else "Alle Projekte",
               "doc_id": (proj or {}).get("gdoc_id", "") if proj else "", "generated": f"{datetime.datetime.now():%d.%m.%Y, %H:%M}",
               "rows": [{"title": r.get("title") or r.get("url"), "url": r.get("url"), "platform": r.get("platform_name"), "uploader": r.get("uploader", ""),
                         "note": r.get("note", ""), "date": de_date(r.get("date")), "file": Path(r["output"]).name if r.get("output") else "",
                         "kind": r.get("kind", "video")} for r in rows]}
    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "ignore")
        res = json.loads(body)
    except Exception as e:
        log(f"Google-Docs-Export fehlgeschlagen: {e}")
        return {"ok": False, "error": f"Google Docs nicht erreichbar: {str(e)[:120]}. Script-Adresse und Freigabe pruefen."}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "Script hat einen Fehler gemeldet")}
    if proj:
        with STATE_LOCK:
            proj["gdoc_id"] = res.get("id", "")
            proj["gdoc_url"] = res.get("url", "")
            save_json(PROJECTS_FILE, PROJECTS)
    return {"ok": True, "url": res.get("url"), "count": len(rows)}


# --------------------------------------------------------------------------- Sonstiges
def reveal(path):
    if IS_MAC:
        subprocess.Popen(["open", "-R", path] if Path(path).is_file() else ["open", path])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(Path(path).parent if Path(path).is_file() else path)])


def tool_check():
    def ver(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip().splitlines()[0][:60]
        except Exception:
            return ""
    return {"ytdlp": shutil.which("yt-dlp") or "", "ffmpeg": shutil.which("ffmpeg") or "", "gallerydl": shutil.which("gallery-dl") or "",
            "ytdlp_version": ver(["yt-dlp", "--version"]), "ffmpeg_version": ver(["ffmpeg", "-version"]).replace("ffmpeg version ", "").split(" ")[0],
            "gallerydl_version": ver(["gallery-dl", "--version"]), "last_update": SETTINGS.get("last_update", 0),
            "missing": [t for t in ("ffmpeg", "yt-dlp", "gallery-dl") if not shutil.which(t)]}


UPDATE = {"running": False, "percent": 0, "message": "", "done": False, "ok": None, "log": ""}
UPDATE_LOCK = threading.Lock()


def _run_step(cmd, p_from, p_to, label, timeout=900):
    """Einen Befehl laufen lassen und den Balken waehrenddessen langsam von p_from nach p_to schieben."""
    UPDATE.update(message=label, percent=p_from)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        UPDATE["log"] += f"{cmd[0]} nicht gefunden\n"
        return False
    n = 0
    start = time.time()
    for line in proc.stdout:
        n += 1
        UPDATE["log"] = (UPDATE["log"] + line)[-4000:]
        # Fortschritt aus Ausgabezeilen und Zeit schaetzen, nie ueber p_to hinaus
        frac = min(0.95, max(n / 60.0, (time.time() - start) / 120.0))
        UPDATE["percent"] = int(p_from + (p_to - p_from) * frac)
        if time.time() - start > timeout:
            proc.kill()
            break
    proc.wait()
    UPDATE["percent"] = p_to
    return proc.returncode == 0


def update_tools():
    """Fehlende Werkzeuge nachinstallieren, vorhandene aktualisieren. Laeuft im Hintergrund, Fortschritt in UPDATE."""
    with UPDATE_LOCK:
        if UPDATE["running"]:
            return
        UPDATE.update(running=True, percent=0, message="Pruefe Werkzeuge ...", done=False, ok=None, log="")
    try:
        brew = shutil.which("brew") or next((b for b in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if Path(b).exists()), "")
        # /opt/homebrew ist immer Apple Silicon: brew dort ausdruecklich als ARM starten,
        # sonst verweigert es die Arbeit, wenn die App selbst unter Rosetta laeuft
        brew_cmd = ["arch", "-arm64", brew] if brew.startswith("/opt/homebrew") and Path("/usr/bin/arch").exists() else [brew]
        UPDATE["log"] += f"brew: {brew or 'nicht gefunden'}\n"
        tools = [("ffmpeg", "ffmpeg"), ("yt-dlp", "yt-dlp"), ("gallery-dl", "gallery-dl")]
        missing = [t for t, b in tools if not shutil.which(t)]
        steps = []
        if brew:
            for t, b in tools:
                if t in missing:
                    steps.append((brew_cmd + ["install", b], f"Installiere {t} ..."))
            steps.append((brew_cmd + ["upgrade", "yt-dlp", "gallery-dl"], "Aktualisiere yt-dlp und gallery-dl ..."))
        else:
            steps.append((["yt-dlp", "-U"], "Aktualisiere yt-dlp ..."))
            if "gallery-dl" in missing:
                steps.append(([sys.executable, "-m", "pip", "install", "-U", "--break-system-packages", "gallery-dl"], "Installiere gallery-dl ..."))
        ok_all = True
        for i, (cmd, label) in enumerate(steps):
            p_from = int(100 * i / len(steps))
            p_to = int(100 * (i + 1) / len(steps)) - 2
            ok = _run_step(cmd, p_from, p_to, label)
            # "brew upgrade" meldet Fehler, wenn schon aktuell: das ist kein Fehler
            if not ok and "already installed" not in UPDATE["log"] and "upgrade" not in cmd:
                ok_all = False
        still_missing = [t for t, b in tools if not shutil.which(t)]
        UPDATE.update(percent=100, done=True, ok=ok_all and not still_missing,
                      message="Alles aktuell." if ok_all and not still_missing else
                      ("Fehlt weiterhin: " + ", ".join(still_missing) if still_missing else "Update mit Fehlern beendet (siehe Protokoll)."))
        SETTINGS["last_update"] = time.time()
        save_json(SETTINGS_FILE, SETTINGS)
        log(f"Update: {UPDATE['message']}")
    except Exception as e:
        UPDATE.update(done=True, ok=False, message=f"Update fehlgeschlagen: {e}", percent=100)
        log(f"Update-Fehler: {e}")
    finally:
        UPDATE["running"] = False


# --------------------------------------------------------------------------- App-Update (GitHub)
APP_UPDATE = {"available": False, "version": "", "notes": "", "checked": 0, "error": ""}


def vtuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])
    except Exception:
        return (0,)


def app_bundle_path():
    """Pfad der laufenden VideoGrab.app (oder None, wenn nicht aus einem Bundle gestartet)."""
    p = APP_DIR
    for _ in range(5):
        if p.suffix == ".app":
            return p
        p = p.parent
    return None


def check_app_update(force=False):
    base = (SETTINGS.get("update_url") or UPDATE_URL).rstrip("/")
    if not force and time.time() - float(SETTINGS.get("last_app_check") or 0) < 20 * 3600:
        return APP_UPDATE
    try:
        import urllib.request
        req = urllib.request.Request(base + "/version.json?t=" + str(int(time.time())), headers={"User-Agent": "VideoGrab/" + VERSION, "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=15) as r:
            info = json.loads(r.read().decode("utf-8", "ignore"))
        latest = str(info.get("version", ""))
        APP_UPDATE.update(available=vtuple(latest) > vtuple(VERSION), version=latest, notes=str(info.get("notes", ""))[:600],
                          zip=info.get("zip") or (base + "/VideoGrab.zip"), checked=time.time(), error="")
        SETTINGS["last_app_check"] = time.time()
        save_json(SETTINGS_FILE, SETTINGS)
    except Exception as e:
        APP_UPDATE.update(error=str(e)[:120], checked=time.time())
        log(f"App-Update-Pruefung fehlgeschlagen: {e}")
    return APP_UPDATE


def install_app_update():
    """Neue Version laden, Bundle ersetzen, App neu starten. Fortschritt in UPDATE."""
    with UPDATE_LOCK:
        if UPDATE["running"]:
            return
        UPDATE.update(running=True, percent=0, message="Lade neue Version ...", done=False, ok=None, log="", kind="app")
    try:
        import urllib.request
        import zipfile as zf
        bundle = app_bundle_path()
        if not bundle:
            raise RuntimeError("VideoGrab laeuft nicht aus einem App-Bundle, Update bitte manuell.")
        url = APP_UPDATE.get("zip") or ((SETTINGS.get("update_url") or UPDATE_URL).rstrip("/") + "/VideoGrab.zip")
        work = TMP_DIR / "appupdate"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        zpath = work / "VideoGrab.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "VideoGrab/" + VERSION})
        with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    UPDATE["percent"] = int(60 * got / total)
        UPDATE.update(percent=60, message="Entpacke ...")
        with zf.ZipFile(zpath) as z:
            z.extractall(work)
        new_app = next((p for p in work.rglob("VideoGrab.app") if (p / "Contents" / "MacOS" / "VideoGrab").exists()), None)
        if not new_app:
            raise RuntimeError("Zip enthaelt keine VideoGrab.app")
        new_ver = re.search(r'VERSION = "([0-9.]+)"', (new_app / "Contents" / "Resources" / "app" / "server.py").read_text("utf-8"))
        UPDATE.update(percent=75, message=f"Installiere Version {new_ver.group(1) if new_ver else '?'} ...")
        for f in (new_app / "Contents" / "MacOS" / "VideoGrab", new_app / "Contents" / "Resources" / "app" / "install.sh",
                  new_app / "Contents" / "Resources" / "app" / "videograb_app.py"):
            try:
                f.chmod(f.stat().st_mode | 0o755)
            except Exception:
                pass
        # Bundle ersetzen: altes beiseite, neues an seinen Platz (gleiches Volume, daher nur Umbenennen)
        old = bundle.with_name("VideoGrab.app.alt")
        shutil.rmtree(old, ignore_errors=True)
        bundle.rename(old)
        try:
            shutil.move(str(new_app), str(bundle))
        except Exception:
            old.rename(bundle)   # zurueck, wenn es schiefgeht
            raise
        shutil.rmtree(old, ignore_errors=True)
        if IS_MAC:
            subprocess.run(["xattr", "-cr", str(bundle)], capture_output=True)
        UPDATE.update(percent=100, done=True, ok=True, message="Neue Version installiert. VideoGrab startet neu ...")
        log(f"App aktualisiert auf {new_ver.group(1) if new_ver else '?'}: {bundle}")
        persist_queue()
        if IS_MAC:
            subprocess.Popen(["bash", "-c", f"sleep 1.5; open -n '{bundle}'"])
            threading.Thread(target=lambda: (time.sleep(1.0), os._exit(0)), daemon=True).start()
    except Exception as e:
        UPDATE.update(done=True, ok=False, message=f"App-Update fehlgeschlagen: {e}", percent=100)
        UPDATE["log"] += str(e)
        log(f"App-Update-Fehler: {e}")
    finally:
        UPDATE["running"] = False


def app_update_loop():
    time.sleep(8)
    check_app_update()


def auto_update_check():
    if SETTINGS.get("auto_update", True) and time.time() - float(SETTINGS.get("last_update") or 0) > 7 * 86400:
        time.sleep(20)   # erst die App starten lassen
        if not RUNNING:
            update_tools()


# --------------------------------------------------------------------------- Hinzufuegen (auch per Clipboard)
def add_urls(urls, pid=None, source="ui"):
    added = []
    with STATE_LOCK:
        known = {i["url"] for i in QUEUE}
        for u in urls:
            if u in known:
                continue
            it = new_item(u, pid)
            QUEUE.append(it)
            added.append(it)
    for it in added:
        threading.Thread(target=fetch_info, args=(it,), daemon=True).start()
    if added:
        persist_queue()
        log(f"{len(added)} Link(s) hinzugefuegt ({source})")
    return added


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype, cache="no-store"):
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._file(UI_DIR / "index.html", "text/html; charset=utf-8")
        if p == "/api/state":
            with STATE_LOCK:
                q = [dict(i, estimate=estimate_bytes(i)) for i in QUEUE]
                return self._json({"queue": q, "running": RUNNING, "version": VERSION, "projects": PROJECTS,
                                   "settings": dict(SETTINGS, home=str(Path.home())), "native": bool(NATIVE["choose_folder"]),
                                   "update": dict(UPDATE), "app_update": dict(APP_UPDATE)})
        if p == "/api/sources":
            with STATE_LOCK:
                return self._json(SOURCES)
        if p == "/api/check":
            return self._json(tool_check())
        if p.startswith("/thumb/"):
            f = (THUMB_DIR / Path(p[7:]).name).resolve()
            if f.parent == THUMB_DIR.resolve():
                return self._file(f, "image/jpeg", cache="public, max-age=31536000, immutable")
            self.send_response(404); self.end_headers(); return
        if p == "/gdoc-script":
            return self._file(APP_DIR / "google-docs-script.gs", "text/plain; charset=utf-8")
        self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            body = {}
        p = self.path.split("?")[0]
        try:
            return self._json(self.route(p, body))
        except Exception as e:
            log(f"API-Fehler {p}: {e}")
            return self._json({"error": str(e)}, 500)

    def route(self, p, b):
        global RUNNING
        if p == "/api/add":
            text = b.get("text", "")
            for f in b.get("files", []):
                try:
                    text += "\n" + text_from_bytes(f.get("name", ""), base64.b64decode(f.get("data", "")))
                except Exception as e:
                    log(f"Datei-Fehler: {e}")
            urls = urls_from_text(text)
            added = add_urls(urls, b.get("project"))
            return {"added": len(added), "found": len(urls)}
        if p == "/api/expand":
            added = []
            with STATE_LOCK:
                src = next((i for i in QUEUE if i["id"] == b.get("id")), None)
                if src and src.get("entries"):
                    idx = QUEUE.index(src)
                    known = {i["url"] for i in QUEUE}
                    for e in src["entries"]:
                        if e["url"] in known:
                            continue
                        n = new_item(e["url"], src.get("project"))
                        n["title"] = e["title"]
                        n["codec"] = src.get("codec")
                        added.append(n)
                    QUEUE[idx:idx + 1] = added
            for n in added:
                threading.Thread(target=fetch_info, args=(n,), daemon=True).start()
            persist_queue()
            return {"added": len(added)}
        if p == "/api/remove":
            ids = set(b.get("ids", []))
            with STATE_LOCK:
                for it in QUEUE:
                    if it["id"] in ids:
                        if it["status"] in ("downloading", "converting", "waiting"):
                            it["status"] = "cancelled"
                            pr = PROCS.get(it["id"])
                            if pr:
                                pr.kill()
                        else:
                            it["status"] = "removed"
                QUEUE[:] = [i for i in QUEUE if i["id"] not in ids]
            persist_queue()
            update_badge()
            return {"ok": True}
        if p == "/api/retry":
            ids = set(b.get("ids", []))
            retry = []
            with STATE_LOCK:
                for it in QUEUE:
                    if it["id"] in ids:
                        it.update(status="info", error="", progress=0.0, vid=None)
                        retry.append(it)
            for it in retry:
                threading.Thread(target=fetch_info, args=(it,), daemon=True).start()
            return {"ok": True}
        if p == "/api/item":
            # Zeilenoptionen: codec, start, end, note, project
            with STATE_LOCK:
                it = next((i for i in QUEUE if i["id"] == b.get("id")), None)
                if not it:
                    return {"ok": False}
                for k in ("codec", "start", "end", "note", "project"):
                    if k in b:
                        it[k] = b[k]
                if b.get("all_codec"):
                    for i in QUEUE:
                        if i["status"] in ("ready", "info", "error"):
                            i["codec"] = b["all_codec"]
            persist_queue()
            return {"ok": True}
        if p == "/api/clear":
            with STATE_LOCK:
                QUEUE[:] = [i for i in QUEUE if i["status"] not in ("done",) and (b.get("errors") is False or i["status"] != "error")]
            persist_queue()
            return {"ok": True}
        if p == "/api/start":
            if b.get("project"):
                SETTINGS["project"] = b["project"]
            save_json(SETTINGS_FILE, SETTINGS)
            if not RUNNING:
                start_downloads()
            update_badge()
            return {"ok": True}
        if p == "/api/cancel_all":
            with STATE_LOCK:
                for it in QUEUE:
                    if it["status"] in ("downloading", "converting", "waiting"):
                        it["status"] = "cancelled"
                        pr = PROCS.get(it["id"])
                        if pr:
                            pr.kill()
            time.sleep(0.3)
            with STATE_LOCK:
                for it in QUEUE:
                    if it["status"] == "cancelled":
                        it.update(status="ready", progress=0.0, speed="", eta="")
            update_badge()
            return {"ok": True}
        if p == "/api/settings":
            for k in ("codec", "browser", "ask_before_start", "parallel", "clipboard_watch", "auto_update", "notify", "gdoc_url", "gdoc_token", "project", "warn_gb", "update_url"):
                if k in b:
                    SETTINGS[k] = b[k]
            save_json(SETTINGS_FILE, SETTINGS)
            return SETTINGS
        # Projekte
        if p == "/api/project_add":
            name = (b.get("name") or "").strip()
            if not name:
                return {"ok": False, "error": "Name fehlt"}
            folder = (b.get("folder") or "").strip() or str(DEFAULT_BASE / safe_name(name))
            pr = {"id": uuid.uuid4().hex[:8], "name": name, "folder": folder, "gdoc_url": "", "gdoc_id": "", "created": now_iso()}
            with STATE_LOCK:
                PROJECTS.append(pr)
                save_json(PROJECTS_FILE, PROJECTS)
            SETTINGS["project"] = pr["id"]
            save_json(SETTINGS_FILE, SETTINGS)
            Path(folder).expanduser().mkdir(parents=True, exist_ok=True)
            return {"ok": True, "project": pr}
        if p == "/api/project_update":
            with STATE_LOCK:
                pr = next((x for x in PROJECTS if x["id"] == b.get("id")), None)
                if not pr:
                    return {"ok": False}
                for k in ("name", "folder", "gdoc_url", "gdoc_id"):
                    if k in b:
                        pr[k] = b[k]
                save_json(PROJECTS_FILE, PROJECTS)
            return {"ok": True, "project": pr}
        if p == "/api/project_delete":
            with STATE_LOCK:
                if len(PROJECTS) <= 1:
                    return {"ok": False, "error": "Das letzte Projekt kann nicht geloescht werden."}
                PROJECTS[:] = [x for x in PROJECTS if x["id"] != b.get("id")]
                save_json(PROJECTS_FILE, PROJECTS)
                for s in SOURCES:   # Quellen bleiben erhalten, wandern ins erste Projekt
                    if s.get("project") == b.get("id"):
                        s["project"] = PROJECTS[0]["id"]
                save_json(SOURCES_FILE, SOURCES)
            if SETTINGS.get("project") == b.get("id"):
                SETTINGS["project"] = PROJECTS[0]["id"]
                save_json(SETTINGS_FILE, SETTINGS)
            return {"ok": True}
        # Quellen
        if p == "/api/source_add":
            url = clean_url(b.get("url") or "")
            if not url.startswith("http"):
                return {"ok": False, "error": "Kein gueltiger Link"}
            key, name = platform_of(url)
            rec = {"id": uuid.uuid4().hex[:10], "project": b.get("project") or SETTINGS.get("project"), "url": url,
                   "title": (b.get("title") or "").strip() or url, "platform": key, "platform_name": name, "output": "", "size": 0,
                   "codec": "", "date": now_iso(), "thumb": "", "duration": None, "uploader": "", "kind": "link", "note": (b.get("note") or "").strip()}
            with STATE_LOCK:
                SOURCES.insert(0, rec)
                save_json(SOURCES_FILE, SOURCES)
            if b.get("fetch_title", True):
                threading.Thread(target=self._title_for_source, args=(rec,), daemon=True).start()
            return {"ok": True, "source": rec}
        if p == "/api/source_update":
            with STATE_LOCK:
                s = next((x for x in SOURCES if x["id"] == b.get("id")), None)
                if not s:
                    return {"ok": False}
                for k in ("note", "title", "project"):
                    if k in b:
                        s[k] = b[k]
                save_json(SOURCES_FILE, SOURCES)
            return {"ok": True}
        if p == "/api/source_move":
            ids = set(b.get("ids", []))
            target = b.get("project")
            if not any(x["id"] == target for x in PROJECTS):
                return {"ok": False, "error": "Projekt nicht gefunden"}
            with STATE_LOCK:
                n = 0
                for s_ in SOURCES:
                    if s_["id"] in ids:
                        s_["project"] = target
                        n += 1
                save_json(SOURCES_FILE, SOURCES)
            return {"ok": True, "moved": n}
        if p == "/api/source_remove":
            ids = set(b.get("ids", []))
            with STATE_LOCK:
                SOURCES[:] = [s for s in SOURCES if s["id"] not in ids]
                save_json(SOURCES_FILE, SOURCES)
            return {"ok": True}
        if p == "/api/export_markdown":
            return export_markdown(b.get("project") or SETTINGS.get("project"), b.get("ids"))
        if p == "/api/export_gdoc":
            return export_gdoc(b.get("project") or SETTINGS.get("project"), b.get("ids"))
        if p == "/api/choose_folder":
            if NATIVE["choose_folder"]:
                return {"folder": NATIVE["choose_folder"](os.path.expanduser(b.get("start") or str(DEFAULT_BASE))) or ""}
            return {"folder": ""}
        if p == "/api/reveal":
            path = os.path.expanduser(b.get("path") or project()["folder"])
            if Path(path).exists():
                reveal(path)
                return {"ok": True}
            Path(path).mkdir(parents=True, exist_ok=True) if b.get("create") else None
            if Path(path).exists():
                reveal(path)
                return {"ok": True}
            return {"ok": False, "error": "Datei oder Ordner nicht mehr vorhanden"}
        if p == "/api/open_url":
            if IS_MAC:
                subprocess.Popen(["open", b.get("url", "")])
            return {"ok": True}
        if p == "/api/update":
            if not UPDATE["running"]:
                threading.Thread(target=update_tools, daemon=True).start()
            return {"ok": True}
        if p == "/api/app_check":
            return check_app_update(force=True)
        if p == "/api/app_update":
            if not UPDATE["running"]:
                threading.Thread(target=install_app_update, daemon=True).start()
            return {"ok": True}
        if p == "/api/update_ack":
            UPDATE.update(done=False, ok=None, percent=0, message="")
            return {"ok": True}
        if p == "/api/quit":
            persist_queue()
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
            return {"ok": True}
        raise ValueError("unbekannte Route " + p)

    @staticmethod
    def _title_for_source(rec):
        """Titel einer manuell eingetragenen Quelle nachholen (Seitentitel oder Video-Titel)."""
        try:
            res = run_ytdlp(["--flat-playlist", "--print", "title", rec["url"]], timeout=60)
            title = res.stdout.strip().splitlines()[0] if res.returncode == 0 and res.stdout.strip() else ""
            if not title:
                import urllib.request
                req = urllib.request.Request(rec["url"], headers={"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/605 Safari/605"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    page = r.read(300_000).decode("utf-8", "ignore")
                m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
                title = html.unescape(m.group(1)).strip() if m else ""
            if title and rec.get("title") == rec["url"]:
                with STATE_LOCK:
                    rec["title"] = re.sub(r"\s+", " ", title)[:120]
                    save_json(SOURCES_FILE, SOURCES)
        except Exception:
            pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(port=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    for d in TMP_DIR.iterdir():
        shutil.rmtree(d, ignore_errors=True)
    ensure_default_project()
    migrate_history()
    restore_queue()
    port = port or free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    log(f"VideoGrab {VERSION} Backend auf http://127.0.0.1:{port}")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=auto_update_check, daemon=True).start()
    threading.Thread(target=app_update_loop, daemon=True).start()
    return port


if __name__ == "__main__":
    port = serve(int(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"http://127.0.0.1:{port}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
