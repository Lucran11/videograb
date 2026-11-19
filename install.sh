#!/bin/bash
# VideoGrab Installation / Update per Terminal:
#   curl -fsSL https://raw.githubusercontent.com/Lucran11/videograb/main/install.sh | bash
set -e
BASE="https://raw.githubusercontent.com/Lucran11/videograb/main"
TMP="$(mktemp -d)"
echo "== VideoGrab: lade aktuelle Version =="
curl -fsSL "$BASE/VideoGrab.zip" -o "$TMP/VideoGrab.zip"
cd "$TMP" && unzip -q VideoGrab.zip
APP="$(find "$TMP" -maxdepth 3 -name 'VideoGrab.app' -type d | head -1)"
[ -n "$APP" ] || { echo "Zip enthaelt keine VideoGrab.app"; exit 1; }
chmod +x "$APP/Contents/MacOS/VideoGrab" "$APP/Contents/Resources/app/install.sh" "$APP/Contents/Resources/app/videograb_app.py"
xattr -cr "$APP" 2>/dev/null || true
osascript -e 'tell application "VideoGrab" to quit' >/dev/null 2>&1 || true
sleep 1
rm -rf "/Applications/VideoGrab.app"
mv "$APP" "/Applications/VideoGrab.app"
echo "App liegt in /Applications/VideoGrab.app"
rm -rf "$TMP"
# Werkzeuge (ffmpeg, yt-dlp, gallery-dl, Python-Umgebung) einrichten bzw. pruefen, danach startet die App
bash "/Applications/VideoGrab.app/Contents/Resources/app/install.sh"
