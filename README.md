# VideoGrab 2.1

Mac-App zum Herunterladen von Videos (YouTube, Instagram, TikTok, ARD, ZDF, Arte, Vimeo, X, Facebook, Twitch, SoundCloud, Direktlinks und über 1000 weitere Seiten) mit direkter Umwandlung in ein Format, das Premiere sauber schneidet.

## Installation per Terminal (empfohlen, kein Gatekeeper-Dialog)

Terminal öffnen, eine Zeile einfügen, Enter:

    curl -fsSL https://raw.githubusercontent.com/Lucran11/videograb/main/install.sh | bash

Lädt die aktuelle App nach /Applications, richtet die Werkzeuge ein und startet VideoGrab. Dieselbe Zeile aktualisiert später auch. Neue Versionen meldet VideoGrab außerdem selbst beim Start, ein Klick installiert sie.

## Erster Start über das Zip (Alternative)

1. Zip entpacken, `VideoGrab.app` doppelklicken. macOS meldet: "Apple konnte nicht überprüfen, ob VideoGrab frei von Schadsoftware ist." Das ist normal für Apps ohne Apple-Signatur. **Auf "Fertig" klicken (nicht Papierkorb).**
2. Systemeinstellungen > Datenschutz & Sicherheit, ganz nach unten scrollen: "VideoGrab wurde blockiert" > **"Trotzdem öffnen"**, mit Passwort bestätigen, im nächsten Dialog "Öffnen".
   Alternativ im Terminal: `xattr -cr ~/Downloads/VideoGrab/VideoGrab.app` (Pfad anpassen), danach Doppelklick.
3. Beim ersten Start öffnet sich das Terminal und installiert alles Nötige (Homebrew falls nicht vorhanden, ffmpeg, yt-dlp, gallery-dl, Python-Umgebung). Dauert je nach Internet 2 bis 10 Minuten, fragt ggf. nach dem Mac-Passwort.
4. Der Installer fragt, ob die App nach /Applications soll (empfohlen). Dann startet VideoGrab.
5. Icon im Dock: Rechtsklick > Optionen > Im Dock behalten.

Alternativ manuell im Terminal: `bash "/Pfad/zu/VideoGrab.app/Contents/Resources/app/install.sh"`

## Neu in 2.1

- **Projekte**: Oben links das Projekt wählen oder "Neues Projekt…" anlegen (Name + Ordner, z. B. auf der Projektplatte). Downloads landen im Projektordner, Quellen werden dem Projekt zugeordnet.
- **Quellen-Tab**: alle Downloads des Projekts mit Link, Datum, Datei und Notiz. "+ Quelle" trägt Links ohne Download ein (Musik, Webseiten). Export als **Markdown** in den Projektordner oder als **Google Doc** (ein Dokument pro Projekt, wird bei jedem Export aktualisiert; Einrichtung in 5 Minuten über Einstellungen > Google Docs > Anleitung).
- **Format pro Zeile** (Dropdown rechts in der Zeile) und "Format für alle…" unten. Der Start-Dialog zeigt die geschätzte Gesamtgröße und warnt ab 20 GB (einstellbar).
- **Ausschnitt** (Schere in der Zeile): nur von/bis laden, z. B. 1:20 bis 2:05. Wenn der Sender das direkte Schneiden blockt, lädt die App komplett und schneidet danach.
- **Bilder**: Direktlinks, Instagram-Fotos und Karussells, Pinterest, X-Bilder über gallery-dl. WebP/AVIF werden zu PNG, Karussells als nummerierte Einzelbilder.
- **Zwischenablage**: Link in Chrome kopieren, er erscheint automatisch in der Liste (abschaltbar).
- **Mitteilung** mit Ton, wenn alle Downloads durch sind; Dock-Zahl zeigt laufende Downloads.
- **Auto-Update** von yt-dlp und gallery-dl einmal pro Woche im Hintergrund.
- Die Warteschlange bleibt nach dem Beenden erhalten.

## Bedienung

- Links ins Feld einfügen (mehrere auf einmal geht), Enter. Oder irgendwo im Fenster ⌘V drücken. Oder eine Datei mit Links (Word, Excel, PDF, Textdatei, Pages, Numbers) ins Fenster ziehen bzw. über "Datei…" wählen.
- Jeder Link bekommt Vorschaubild, Titel, Plattform-Kennzeichen und Dauer. Fehler (Login nötig, gelöscht, Geoblocking, DRM, keine Videoseite) stehen direkt am Eintrag.
- Playlists oder Kanalseiten werden erkannt: ein Klick auf "Alle hinzufügen" übernimmt die einzelnen Videos (max. 200).
- "Download starten": zeigt Projekt, Ordner und geschätzte Größe. "Nicht mehr fragen" kann man in den Einstellungen wieder aktivieren.
- Fortschritt pro Video (Download blau, Umwandlung lila, fertig grün) plus Gesamtbalken. "Stopp" bricht alles ab.
- Im Zielordner liegen am Ende wirklich nur die fertigen Videos. Zwischendateien liegen in `~/Library/Application Support/VideoGrab/tmp` und werden automatisch gelöscht.
- Quellen-Tab: jeder fertige Download mit Link, Datum, Größe, Notiz. "Im Finder", "Link kopieren", "Laden".

## Formate

| Format | Wofür |
|---|---|
| ProRes 422 HQ (.mov) | Standard. Bester Schnittcodec für Premiere, 10 bit 4:2:2, Ton PCM 24 bit 48 kHz. Groß: ca. 1,5 GB pro Minute 1080p. Auf Apple Silicon per Hardware-Encoder sehr schnell. |
| ProRes 422 LT (.mov) | Gleiche Schnittqualität im Alltag, etwa halbe Dateigröße. Gute Wahl für lange Mediathek-Sendungen. |
| H.264 (.mp4) | Kleine Dateien, schneidet in Premiere gut. Für Material, das nur gesichtet wird. |
| HEVC / H.265 (.mp4) | Kleinste Dateien, Premiere kann es, scrubbt aber träger. |
| Original | Keine Umwandlung, so wie die Seite es liefert (oft VP9 oder HEVC, nicht immer Premiere-tauglich). |
| Nur Ton (.wav) | Tonspur als WAV 48 kHz 24 bit. |

Alle Varianten: Framerate wie Quelle (fest, kein variables Timing), Auflösung unverändert, Rec.709.

## Login-Seiten

Instagram, private Vimeos, Reddit und manchmal YouTube ("Bot-Sperre") verlangen einen Login. VideoGrab versucht erst ohne, bei Bedarf nimmt es automatisch die Cookies aus Chrome (umstellbar in den Einstellungen). Dafür in Chrome auf der Seite eingeloggt sein. Beim ersten Mal fragt macOS nach dem Schlüsselbund-Passwort für die Chrome-Cookies: "Immer erlauben" klicken, dann kommt die Frage nicht wieder.

## Wenn eine Seite nicht mehr geht

Die Plattformen ändern regelmäßig ihre Technik. Erste Hilfe ist immer: Einstellungen > yt-dlp "Aktualisieren". Das Projekt yt-dlp zieht meist innerhalb weniger Tage nach.

Nicht möglich (DRM): Netflix, Disney+, Prime Video, Apple TV, Sky/WOW, Joyn, RTL+, DAZN, Spotify. Das sagt die App direkt beim Hinzufügen.

Protokoll: `~/Library/Application Support/VideoGrab/videograb.log` (Einstellungen > Im Finder zeigen).

## Weitergabe im Team

Den Ordner `VideoGrab` (mit der App) kopieren oder per AirDrop/Cloud schicken. Jeder Mac macht den ersten Start einmal (Installation). Einstellungen und Archiv sind pro Nutzer.
