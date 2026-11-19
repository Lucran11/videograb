#!/usr/bin/env python3
"""VideoGrab Fenster: startet das Backend, zeigt die Oberflaeche, Mitteilungen, Dock-Zahl, Zwischenablage."""
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
import server  # noqa: E402

IS_MAC = sys.platform == "darwin"


def notify(title, msg):
    if IS_MAC:
        safe = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'display notification "{safe(msg)}" with title "{safe(title)}" sound name "Glass"'])


def badge(text):
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication
        from PyObjCTools import AppHelper
        AppHelper.callAfter(lambda: NSApplication.sharedApplication().dockTile().setBadgeLabel_(text or None))
    except Exception:
        pass


def clipboard_watcher():
    """Wie bei JDownloader: kopierter Link landet automatisch in der Liste."""
    last = None
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        last_count = pb.changeCount()
    except Exception:
        pb = None
        last_count = None
    while True:
        time.sleep(1.0)
        try:
            if not server.SETTINGS.get("clipboard_watch", True):
                continue
            if pb is not None:
                c = pb.changeCount()
                if c == last_count:
                    continue
                last_count = c
                text = pb.stringForType_(NSPasteboardTypeString) or ""
            else:
                text = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout if IS_MAC else ""
                if text == last:
                    continue
                last = text
            text = str(text)
            if len(text) > 20000 or "http" not in text:
                continue
            urls = [u for u in server.urls_from_text(text) if server.platform_of(u)[0] not in ("web",) or u.lower().endswith(server.IMAGE_EXT)]
            if urls:
                server.add_urls(urls, source="Zwischenablage")
        except Exception:
            pass


def _dark():
    try:
        return "Dark" in subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True).stdout
    except Exception:
        return False


def main():
    server.NATIVE["notify"] = notify
    server.NATIVE["badge"] = badge
    port = server.serve()
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=clipboard_watcher, daemon=True).start()
    try:
        import webview  # pywebview
    except ImportError:
        server.log("pywebview fehlt, oeffne im Browser")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    window = webview.create_window("VideoGrab", url, width=960, height=680, min_size=(760, 500),
                                   text_select=False, background_color="#1c1c1e" if _dark() else "#f5f5f7")

    def choose_folder(start):
        res = window.create_file_dialog(webview.FOLDER_DIALOG, directory=start or str(Path.home() / "Downloads"))
        if res:
            return res[0] if isinstance(res, (list, tuple)) else str(res)
        return ""

    server.NATIVE["choose_folder"] = choose_folder
    webview.start(debug=False)
    server.persist_queue()
    os._exit(0)


if __name__ == "__main__":
    main()
