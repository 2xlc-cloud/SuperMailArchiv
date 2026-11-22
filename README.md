# SuperMailBackup

SuperMailBackup archiviert E-Mails mehrerer IMAP-Konten in einer lokalen SQLite-Datenbank und bietet eine Offline-Suche mit Mailvorschau. Die Oberfläche folgt dem Design von SuperPyBackup. Beim ersten Start ist der Dunkelmodus aktiv. Oben rechts lässt sich die Darstellung umschalten.

## Starten

Die gebaute Windows-EXE lässt sich per Doppelklick starten. Für sie ist keine separate Python-Installation erforderlich.

Aus dem Quellcode starten:

1. Unter Windows Python 3.10 oder neuer mit Tkinter installieren.
2. Das Repository klonen oder über GitHub als ZIP herunterladen und entpacken.
3. [Start-SuperMailBackup.cmd](Start-SuperMailBackup.cmd) per Doppelklick ausführen. Python-Zusatzpakete sind nicht nötig.
4. Im Reiter **Konten** IMAP-Server, TLS-Port und Benutzername hinterlegen. Standardmäßig verwendet die App Port 993.
5. Das Passwort oder App-Passwort eingeben und **Abrufen** wählen. Konten mit ausschließlich OAuth werden derzeit nicht unterstützt.

## Archiv

Die App sichert die Ordner eines IMAP-Kontos, etwa Posteingang, Gesendet, Entwürfe, Archiv und eigene Ordner. Welche Ordner archiviert werden, legst du im Reiter **Konten** pro Konto in einem Ordnerbaum mit Häkchen fest. Die Auswahl lässt sich jederzeit ändern und gilt ab dem nächsten Abruf. Ein Klick schaltet nur den angeklickten Ordner. So lässt sich etwa der Posteingang allein an- oder abwählen, auch wenn der Server alle anderen Ordner darunter einordnet. Ein Rechtsklick bietet an, einen Ordner mit allen Unterordnern zu schalten. Abgewählte Ordner behalten ihre bereits gesicherten Mails. **Ordnerliste vom Server laden** holt die Ordner, ohne Mails abzurufen, damit du schon vor dem ersten Abruf auswählen kannst. Neben jedem Ordner stehen drei Zahlen: **Auf Server** ist die Zahl der Mails im Ordner beim letzten Laden der Ordnerliste oder Abruf, **Gelöscht markiert** wie viele davon nur als gelöscht markiert sind, und **Archiviert** die Zahl der im Archiv gesicherten Mails. Unterordner werden jeweils getrennt gezählt. Während eines Abrufs aktualisieren sich die Zahlen laufend.

Viele Mailprogramme löschen per IMAP nicht sofort, sondern markieren Mails nur als gelöscht und blenden sie aus. Auf dem Server bleiben sie im Ordner, bis das Programm sie endgültig entfernt (etwa „Ordner komprimieren“ oder „Gelöschte Elemente bereinigen“). Ist bei einem Konto **Als gelöscht markierte Mails nicht archivieren** gesetzt (Standard), lässt die App solche Mails aus. Bereits archivierte Mails bleiben im Archiv.

Unter **Zeitraum für die Archivierung** legst du pro Konto fest, von wann bis wann Mails archiviert werden. Von und Bis können jeweils **Unbegrenzt**, **Heute**, **Gestern**, **Vor 1 Woche** oder ein bestimmter **Monat** mit **Jahr** sein. Ein Monat als Von zählt ab dem Ersten, als Bis bis einschließlich zum Monatsletzten. Heute, Gestern und Vor 1 Woche werden bei jedem Abruf neu berechnet. Von „Vor 1 Woche“ bis „Heute“ sichert also immer die Mails der letzten 7 Tage. Maßgeblich ist das Eingangsdatum auf dem Server. Bereits gesicherte Mails außerhalb des Zeitraums bleiben im Archiv.

Anfangs ausgewählt sind alle Ordner außer Spam, Papierkorb und bei Gmail den Sammelordnern „Alle Nachrichten“ und „Markiert“, damit keine Mails doppelt im Archiv landen. Neue Ordner auf dem Server folgen derselben Regel.

- Links im Ordnerbaum ein Konto oder einen Ordner auswählen. Hinter jedem Ordner steht die Zahl der gesicherten Mails.
- Nach Betreff, Absender, Empfänger, Text oder Mail-ID suchen. Die Suche nutzt einen Volltextindex (Trigramme) und findet auch Wortteile: „rechnung“ findet „Telefonrechnung“, „muller“ findet „Müller“, Groß-/Kleinschreibung spielt keine Rolle. Mehrere Wörter müssen alle vorkommen. Ab drei Zeichen antwortet die Suche in Millisekunden, kürzere Suchbegriffe allein dauern bei großen Archiven etwas länger. Den Index baut die App für vorhandene Archive einmalig im Hintergrund auf (Fortschritt in der Statuszeile, bei rund 50.000 Mails etwa 2 Minuten); bis dahin werden noch nicht erfasste Mails langsamer durchsucht. Der Index braucht etwa doppelt so viel Platz wie der Text der Mails.
- Die Mailliste zeigt eine Klammer 📎 für Mails mit Anhängen, Betreff, Absender, Datum, Uhrzeit und Größe der Mail. Die Größe steht auch im Kopf der Vorschau. Ein Klick auf eine Spaltenüberschrift sortiert danach, ein weiterer Klick kehrt die Reihenfolge um (▲ aufsteigend, ▼ absteigend). Texte werden wie im Wörterbuch sortiert (Ä wie A), Datum nach Zeitpunkt, Uhrzeit nach Tageszeit. Die Sortierung bleibt für den nächsten Start gespeichert.
- Bei Mails, die vor Version 1.12.0 archiviert wurden, ermittelt die App die Klammer einmalig im Hintergrund, solange sie läuft: zuerst für die gerade angezeigten Mails, dann für alle übrigen von neu nach alt (etwa 1.000 Mails in 2 Sekunden). Den Fortschritt zeigt die Statuszeile.
- Spalte **Spam**: Bewertung des Mailservers (rspamd/SpamAssassin, DMARC) aus den Kopfzeilen; ⚠ markiert verdächtige Mails (ab einstellbarer Punktzahl, Standard 5, oder bei DMARC fail bzw. Server-Markierung). Schalter **Nur verdächtige ⚠** neben der Suche; die Vorschau nennt die Gründe. Es wird nichts gelöscht oder aussortiert.
- Rechtsklick auf eine Mail → **Nur Mails von … anzeigen** filtert auf die Adresse dieses Absenders, unabhängig vom angezeigten Namen. Der Filter wirkt zusammen mit Suche, Ordner und Sortierung. Neben dem Suchfeld steht dann „Von: … ✕“; ein Klick darauf hebt den Filter auf.
- Jede Nachricht anhand ihrer eindeutigen Mail-ID identifizieren und bei Bedarf als EML exportieren.
- Links in der Nachricht (http, https, mailto, www.) sind anklickbar und öffnen sich im Standardbrowser bzw. Mailprogramm. Beim Darüberfahren zeigt die Statuszeile das Ziel. Bei HTML-Mails steht das tatsächliche Linkziel sichtbar hinter dem Linktext.
- Bild- und PDF-Anhänge zeigen im Reiter **Anhänge** eine kleine Vorschau, PDFs die erste Seite. Ein Klick darauf oder **Vorschau** öffnet ein scrollbares Vorschaufenster: Bilder in voller Breite, PDFs mit bis zu 30 Seiten untereinander. Gescrollt wird mit den Scrollbalken, dem Mausrad (Umschalt + Mausrad seitwärts) oder Bild↑/Bild↓, Pos1/Ende; Esc schließt das Fenster. Die Bilder stammen von Windows (wie im Explorer); PDFs rendert der in Windows 10/11 eingebaute PDF-Renderer. Text-Anhänge lassen sich ebenfalls ansehen, weitere gängige Formate über die zugeordnete Windows-Anwendung öffnen.
- Zum Öffnen erzeugte Kopien von Anhängen löscht die App beim nächsten Start, sobald sie älter als einen Tag sind.

## Einstellungen

Im Reiter **Einstellungen** lässt sich festlegen:

- **Im Infobereich weiterlaufen:** Beim Schließen des Fensters läuft die App mit einem Symbol im Infobereich neben der Uhr weiter. Ein Klick auf das Symbol öffnet das Fenster wieder, Rechtsklick → **Beenden** beendet die App. Standardmäßig eingeschaltet.
- **Mit Windows starten:** Trägt die App für das aktuelle Windows-Benutzerkonto in den Autostart ein. Optional startet sie dabei nur im Hintergrund. Wird die EXE an einen anderen Ort verschoben, bietet der Reiter an, den Autostart anzupassen.
- **Automatischer Abruf:** Aus, alle 10 Minuten, alle 30 Minuten, jede Stunde oder alle 2 Stunden. Abgerufen werden alle Konten mit gespeichertem Passwort, der erste Abruf eine Minute nach dem Programmstart. Während eines Abrufs ist das Symbol orange. Fehler meldet die App in der Statuszeile oder, wenn das Fenster verborgen ist, als Benachrichtigung.
- **Darstellung:** Dunkel oder Hell.
- **Entwicklermodus:** Nach einer Rückfrage lassen sich archivierte Mails, Ordner und Konten aus dem lokalen Archiv löschen. Die App zeigt dann oben rechts „Entwicklermodus“ an. Gelöscht wird per Rechtsklick im Reiter **Archiv**, mit der Entf-Taste oder über **Aus Archiv löschen …**:
  - **Mails:** eine oder mehrere markierte Mails.
  - **Ordner:** die Mails eines Ordners, auf Wunsch mit allen Unterordnern. Die Ordnerauswahl bleibt erhalten.
  - **Konto:** das Konto mit gespeichertem Passwort, Ordnerauswahl, Zeitraum und allen Mails.

  Auf dem Mailserver wird nichts gelöscht. Liegen gelöschte Mails dort noch und passen sie zu Ordnerauswahl und Zeitraum, archiviert der nächste Abruf sie erneut. Gelöschte Inhalte werden in der Archivdatei überschrieben.

Ein erneuter Start der App öffnet das bereits laufende Fenster, statt eine zweite Instanz zu starten.

## Datenbank

Der Reiter **Datenbank** zeigt und pflegt die Archivdatei:

- **Archivdatei:** Pfad der geöffneten Datei, **Im Explorer zeigen**, **Anderes Archiv öffnen …** (z. B. auf einem anderen Laufwerk), **Neues Archiv anlegen …** und **Standardarchiv verwenden**. Beim Wechsel startet die App neu. Konten, gespeicherte Passwörter, Ordnerauswahl und Zeitraum gehören zum jeweiligen Archiv. Fehlt das gewählte Archiv beim Start (etwa ein nicht angeschlossenes Laufwerk), fragt die App, ob sie stattdessen das Standardarchiv öffnen soll; sie legt dort nie stillschweigend ein leeres Archiv an.
- **Übersicht:** Dateigröße, ungenutzter Platz, Anzahl Mails, Konten und Ordner, Zeitraum der Mails, Stand des Suchindex, freier Speicher und Format.
- **Sicherung erstellen …:** Schreibt eine vollständige Kopie an einen frei wählbaren Ort. Die Kopie entsteht zuerst als `.teil`-Datei, wird geprüft und erst dann umbenannt; ein Abbruch hinterlässt keine halbe Sicherung.
- **Sicherung wiederherstellen …:** Ersetzt das geöffnete Archiv nach einer Rückfrage durch eine Sicherung. Das geschieht beim Neustart, bevor die App das Archiv benutzt. Das bisherige Archiv bleibt daneben als `archiv-vor-Wiederherstellung <Datum>.sqlite3` erhalten. Gespeicherte Passwörter lassen sich nur unter demselben Windows-Benutzer entschlüsseln.
- **Schnell prüfen / Vollständig prüfen:** Prüft die Datei (SQLite `quick_check` bzw. `integrity_check`), die Verweise zwischen Mails und Konten und den Suchindex.
- **Komprimieren:** Schreibt die Datei neu und gibt ungenutzten Platz frei (VACUUM), z. B. nach dem Löschen im Entwicklermodus.
- **Suchindex neu aufbauen:** Leert den Suchindex; die App baut ihn im Hintergrund neu auf.

Während eines Abrufs ist keine Sicherung oder Wartung möglich und umgekehrt; der automatische Abruf wartet. Prüfen, Komprimieren und Sichern lassen sich abbrechen; das Archiv bleibt dann unverändert.

## Updates

Die Updateroutine stammt aus SuperPyBackup. Die App prüft beim Start und danach alle 12 Stunden, ob es auf GitHub (2xlc-cloud/SuperMailArchiv) eine neuere Release gibt. Abschalten lässt sich das unter **Einstellungen → Updates**. Beim Prüfen wird nie etwas heruntergeladen. Eine neue Version meldet die App einmal je Version in der Statuszeile und, wenn das Fenster verborgen ist, neben der Uhr.

**Update installieren …** lädt die neue Version erst nach Bestätigung herunter und installiert sie nur, wenn alle Prüfungen bestehen:

- Download nur über HTTPS und nur aus Releases des eigenen Repositorys
- Ed25519-Signatur über SHA256SUMS.txt mit dem fest eingebauten öffentlichen Schlüssel – ein manipuliertes Repository oder ein übernommenes GitHub-Konto genügt nicht, um fremde Programme einzuspielen
- Größe und SHA-256 der EXE
- Probelauf der neuen EXE mit `--version`

Danach wird die laufende EXE ersetzt; die bisherige bleibt als SuperMailBackup.exe.old erhalten (**Vorversion wiederherstellen**). Bei jedem Fehler bleibt die installierte Version unverändert. Liegt die installierte Version mehr als 5 veröffentlichte Versionen zurück, installiert die App das Update als **Pflichtupdate** ohne Rückfrage, mit denselben Prüfungen. Updates gibt es nur für die EXE, nicht beim Start aus dem Quellcode.

## Lokale Daten

Die Datenbank liegt standardmäßig unter %LOCALAPPDATA%\MailArchiv\archiv.sqlite3 (anderer Ort: Reiter **Datenbank**), die Einstellungen immer in %LOCALAPPDATA%\MailArchiv\einstellungen.json. Der Datenordner behält seinen bisherigen Namen, damit vorhandene Archive direkt weiterverwendet werden.

Ab Version 1.3.0 speichert das Archiv zu jeder Mail ihren IMAP-Ordner. Beim ersten Start wird ein vorhandenes Archiv umgestellt. Frühere Konten mit gleichem Server und Benutzernamen, aber verschiedenen Ordnern, werden dabei zu einem Konto zusammengeführt. Ältere Programmversionen können das umgestellte Archiv nicht mehr öffnen. Die App legt deshalb vorher eine Kopie unter archiv-vor-1.3.sqlite3 ab, die nicht mehr benötigt wird, sobald alles funktioniert.

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

Der Build erzeugt die Datei **dist/SuperMailBackup.exe**. Die Architektur der EXE entspricht der verwendeten Python-Installation. Daneben entstehen **dist/SHA256SUMS.txt** und, wenn der Signaturschlüssel vorhanden ist, **dist/SHA256SUMS.txt.sig**.

### Release veröffentlichen

Der geheime Signaturschlüssel liegt außerhalb des Repositorys unter `%USERPROFILE%\.supermailbackup\release-signing.key`; nur das eigene Windows-Konto hat Zugriff. Er gehört in eine Datensicherung: Geht er verloren, akzeptieren bestehende Installationen keine neuen Versionen mehr. Ein neuer Schlüssel entsteht nur mit `python tools\sign_release.py keygen`; sein öffentlicher Teil muss dann in `updater.RELEASE_PUBLIC_KEYS` stehen.

Eine Release braucht alle drei Dateien, als Tag die Versionsnummer mit vorangestelltem `v`:

~~~powershell
python build_exe.py
gh release create v1.14.0 dist\SuperMailBackup.exe dist\SHA256SUMS.txt dist\SHA256SUMS.txt.sig --title "SuperMailBackup 1.14.0"
~~~
