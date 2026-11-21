# SuperMailBackup

SuperMailBackup archiviert E-Mails mehrerer IMAP-Konten in einer lokalen SQLite-Datenbank und bietet eine Offline-Suche mit Mailvorschau. Die Oberfläche folgt dem Design von SuperPyBackup. Beim ersten Start ist der Dunkelmodus aktiv. Oben rechts lässt sich die Darstellung umschalten.

## Starten

Die gebaute Windows-EXE lässt sich per Doppelklick starten. Für sie ist keine separate Python-Installation erforderlich.

Aus dem Quellcode starten:

1. Unter Windows Python 3.10 oder neuer mit Tkinter installieren.
2. Das Repository klonen oder über GitHub als ZIP herunterladen und entpacken.
3. [Start-SuperMailBackup.cmd](Start-SuperMailBackup.cmd) per Doppelklick ausführen. Python-Zusatzpakete sind nicht nötig.
4. Im Reiter **Konten** IMAP-Server, TLS-Port, Benutzername und Ordner hinterlegen. Standardmäßig verwendet die App Port 993 und den Ordner INBOX.
5. Das Passwort oder App-Passwort eingeben und **Abrufen** wählen. Konten mit ausschließlich OAuth werden derzeit nicht unterstützt.

## Archiv

- Nach Konto filtern und nach Betreff, Absender, Empfänger, Text oder Mail-ID suchen.
- Jede Nachricht anhand ihrer eindeutigen Mail-ID identifizieren und bei Bedarf als EML exportieren.
- Text-, PNG- und GIF-Anhänge in der App ansehen. Weitere gängige Formate über die zugeordnete Windows-Anwendung öffnen.

## Lokale Daten

Die Datenbank liegt unter %LOCALAPPDATA%\MailArchiv\archiv.sqlite3. Der Datenordner behält seinen bisherigen Namen, damit vorhandene Archive direkt weiterverwendet werden.

IMAP-Kontodaten und optional gespeicherte Passwörter schützt Windows per DPAPI für das aktuelle Windows-Benutzerkonto. Mailinhalte und Anhänge sind in der Datenbank unverschlüsselt.

## Versionierung und EXE-Build

Die Versionsnummer steht zentral in [app_info.py](app_info.py). Sie erscheint im Fenstertitel und in den Dateieigenschaften der EXE. Die erste Version ist **1.0.0**.

| Änderung | Beispiel ab 1.0.0 |
| --- | --- |
| Großer Versionssprung | 2.0.0 |
| Kleinere neue Funktionen | 1.1.0 |
| Fehlerkorrekturen | 1.0.1 |

Unter Windows im Projektordner ausführen:

~~~powershell
python -m pip install pyinstaller
python build_exe.py
~~~

Der Build erzeugt die Datei **dist/SuperMailBackup.exe**. Die Architektur der EXE entspricht der verwendeten Python-Installation.
