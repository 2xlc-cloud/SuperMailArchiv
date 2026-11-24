# SuperNMailArchiv

SuperNMailArchiv (bis Version 1.22.0: SuperMailBackup) archiviert E-Mails mehrerer IMAP-Konten in einer lokalen SQLite-Datenbank und bietet eine Offline-Suche mit Mailvorschau. Die Oberfläche folgt dem Design von SuperPyBackup. Beim ersten Start ist das dunkle Design „Nachtblau“ aktiv; oben rechts unter **Design …** stehen 20 Designs zur Wahl.

## Starten

Die gebaute Windows-EXE lässt sich per Doppelklick starten. Für sie ist keine separate Python-Installation erforderlich.

Aus dem Quellcode starten:

1. Unter Windows Python 3.10 oder neuer mit Tkinter installieren.
2. Das Repository klonen oder über GitHub als ZIP herunterladen und entpacken.
3. Für die HTML-Ansicht einmalig `python -m pip install tkinterweb` ausführen (ohne das Paket läuft die App, zeigt Mails aber nur als Text).
4. [Start-SuperNMailArchiv.cmd](Start-SuperNMailArchiv.cmd) per Doppelklick ausführen.
5. Im Reiter **Konten** IMAP-Server, TLS-Port und Benutzername hinterlegen. Standardmäßig verwendet die App Port 993.
6. Das Passwort oder App-Passwort eingeben und **Abrufen** wählen. Konten mit ausschließlich OAuth werden derzeit nicht unterstützt.

## Archiv

Die App sichert die Ordner eines IMAP-Kontos, etwa Posteingang, Gesendet, Entwürfe, Archiv und eigene Ordner. Welche Ordner archiviert werden, legst du im Reiter **Konten** pro Konto in einem Ordnerbaum mit Häkchen fest. Die Auswahl lässt sich jederzeit ändern und gilt ab dem nächsten Abruf. Ein Klick schaltet nur den angeklickten Ordner. So lässt sich etwa der Posteingang allein an- oder abwählen, auch wenn der Server alle anderen Ordner darunter einordnet. Ein Rechtsklick bietet an, einen Ordner mit allen Unterordnern zu schalten. Abgewählte Ordner behalten ihre bereits gesicherten Mails. **Ordnerliste vom Server laden** holt die Ordner, ohne Mails abzurufen, damit du schon vor dem ersten Abruf auswählen kannst. Neben jedem Ordner stehen drei Zahlen: **Auf Server** ist die Zahl der Mails im Ordner beim letzten Laden der Ordnerliste oder Abruf, **Gelöscht markiert** wie viele davon nur als gelöscht markiert sind, und **Archiviert** die Zahl der im Archiv gesicherten Mails. Unterordner werden jeweils getrennt gezählt. Während eines Abrufs aktualisieren sich die Zahlen laufend.

Viele Mailprogramme löschen per IMAP nicht sofort, sondern markieren Mails nur als gelöscht und blenden sie aus. Auf dem Server bleiben sie im Ordner, bis das Programm sie endgültig entfernt (etwa „Ordner komprimieren“ oder „Gelöschte Elemente bereinigen“). Ist bei einem Konto **Als gelöscht markierte Mails nicht archivieren** gesetzt (Standard), lässt die App solche Mails aus. Bereits archivierte Mails bleiben im Archiv.

Unter **Zeitraum für die Archivierung** legst du pro Konto fest, von wann bis wann Mails archiviert werden. Von und Bis können jeweils **Unbegrenzt**, **Heute**, **Gestern**, **Vor 1 Woche** oder ein bestimmter **Monat** mit **Jahr** sein. Ein Monat als Von zählt ab dem Ersten, als Bis bis einschließlich zum Monatsletzten. Heute, Gestern und Vor 1 Woche werden bei jedem Abruf neu berechnet. Von „Vor 1 Woche“ bis „Heute“ sichert also immer die Mails der letzten 7 Tage. Maßgeblich ist das Eingangsdatum auf dem Server. Bereits gesicherte Mails außerhalb des Zeitraums bleiben im Archiv.

Anfangs ausgewählt sind alle Ordner außer Spam, Papierkorb und bei Gmail den Sammelordnern „Alle Nachrichten“ und „Markiert“, damit keine Mails doppelt im Archiv landen. Neue Ordner auf dem Server folgen derselben Regel.

- Links im Ordnerbaum ein Konto oder einen Ordner auswählen. Hinter jedem Ordner steht die Zahl der gesicherten Mails.
- **◀ Ordner** (oder Strg+B) klappt den Bereich mit Konten und Ordnern ein, damit die Mailliste mehr Platz hat; eingeklappt nennt der Knopf das gewählte Konto und den Ordner, ein Klick klappt ihn wieder aus.
- **Vorschau unten / Vorschau rechts** stellt die Mailvorschau unter die Liste oder daneben. Unter der Liste zeigt sie nur die Nachricht und die Anhänge; Absender, Datum, Konto, Spam-Bewertung und Mail-ID stehen im Detailfenster (Doppelklick). Die Blätterknöpfe **◀ Zurück / Weiter ▶** stehen klein über der Liste. Beide Einstellungen bleiben für den nächsten Start gespeichert.
- Nach Betreff, Absender, Empfänger, Text oder Mail-ID suchen. Die Suche nutzt einen Volltextindex (Trigramme) und findet auch Wortteile: „rechnung“ findet „Telefonrechnung“, „muller“ findet „Müller“, Groß-/Kleinschreibung spielt keine Rolle. Mehrere Wörter müssen alle vorkommen. Ab drei Zeichen antwortet die Suche in Millisekunden, kürzere Suchbegriffe allein dauern bei großen Archiven etwas länger. Den Index baut die App für vorhandene Archive einmalig im Hintergrund auf (Fortschritt in der Statuszeile, bei rund 50.000 Mails etwa 2 Minuten); bis dahin werden noch nicht erfasste Mails langsamer durchsucht. Der Index braucht etwa doppelt so viel Platz wie der Text der Mails.
- Die Mailliste zeigt eine Klammer 📎 für Mails mit Anhängen, Betreff, Absender, Datum, Uhrzeit (Spalte **Zeit**) und Größe (Spalte **Gr**) der Mail; die kurzen Spalten sind genau so breit wie ihr Inhalt. Die Größe steht auch im Kopf der Vorschau. Ein Klick auf eine Spaltenüberschrift sortiert danach, ein weiterer Klick kehrt die Reihenfolge um (▲ aufsteigend, ▼ absteigend). Texte werden wie im Wörterbuch sortiert (Ä wie A), Datum nach Zeitpunkt, Uhrzeit nach Tageszeit. Die Sortierung bleibt für den nächsten Start gespeichert.
- Bei Mails, die vor Version 1.12.0 archiviert wurden, ermittelt die App die Klammer einmalig im Hintergrund, solange sie läuft: zuerst für die gerade angezeigten Mails, dann für alle übrigen von neu nach alt (etwa 1.000 Mails in 2 Sekunden). Den Fortschritt zeigt die Statuszeile.
- Spalte **⚠** (Spam): Bewertung des Mailservers (rspamd/SpamAssassin, DMARC) aus den Kopfzeilen als Symbol: ⚠ verdächtig (ab einstellbarer Punktzahl, Standard 5, oder bei DMARC fail bzw. Server-Markierung), ✓ bewertet und unauffällig, leer ohne Bewertung. Punktzahl und Gründe zeigen Vorschau und Detailfenster; die Spalte sortiert verdächtige Mails zuerst und dann nach Punktzahl. Schalter **Nur verdächtige ⚠** neben der Suche; die Vorschau nennt die Gründe. Es wird nichts gelöscht oder aussortiert.
- Rechtsklick auf eine Mail → **Nur Mails von … anzeigen** filtert auf die Adresse dieses Absenders, unabhängig vom angezeigten Namen. Der Filter wirkt zusammen mit Suche, Ordner und Sortierung. Neben dem Suchfeld steht dann „Von: … ✕“; ein Klick darauf hebt den Filter auf.
- Jede Nachricht anhand ihrer eindeutigen Mail-ID identifizieren und bei Bedarf als EML exportieren.
- **Doppelklick** auf eine Mail (oder Enter, oder Rechtsklick → **In eigenem Fenster öffnen**) öffnet ein eigenes Fenster mit allen Details: Von, An, Cc, Antwort an, Datum, Größe, Konto, Ordner, Spam-Bewertung und Mail-ID, darunter die Reiter **HTML** (bei HTML-Mails, vorausgewählt), **Nachricht** (Text mit anklickbaren Links), **Anhänge** und **Kopfzeilen** (alle Kopfzeilen im Original). **Original als .eml speichern …** und **Im Mailprogramm öffnen** (z. B. Outlook oder Thunderbird) arbeiten mit einer Kopie des Originals. Mehrere Mails lassen sich nebeneinander öffnen; Esc schließt das Fenster.
- **Erneut zusenden …** (Rechtsklick in der Liste, auch für mehrere markierte Mails, Knopf in der Vorschau rechts oder im Detailfenster) legt eine Kopie der archivierten Mail per IMAP als ungelesene neue Mail in den Posteingang des Kontos, aus dem sie archiviert wurde. Der Betreff beginnt mit **[Archiv]**, Inhalt und Anhänge bleiben unverändert. Die Kopie trägt die Kopfzeile `X-SuperMailBackup-Resent` und wird beim nächsten Abruf nicht noch einmal archiviert. Ist kein Passwort gespeichert, fragt die App danach.
- Die **HTML-Ansicht** stellt Layout, Tabellen, Farben und die in der Mail enthaltenen Bilder dar (Tkhtml über tkinterweb; modernes CSS nur eingeschränkt). Skripte laufen nie, Formulare, eingebettete Seiten (iframe) und automatische Weiterleitungen sind abgeschaltet, Links öffnen sich nur per Klick im Standardbrowser. **Bilder aus dem Internet** werden zunächst blockiert, damit Absender über Tracking-Pixel nicht erfahren, dass und wann die Mail geöffnet wurde; **Externe Bilder laden** holt sie für die angezeigte Mail nach.
- Links in der Nachricht (http, https, mailto, www.) sind anklickbar und öffnen sich im Standardbrowser bzw. Mailprogramm. Beim Darüberfahren zeigt die Statuszeile das Ziel. Bei HTML-Mails steht das tatsächliche Linkziel sichtbar hinter dem Linktext.
- Bild- und PDF-Anhänge zeigen im Reiter **Anhänge** eine kleine Vorschau, PDFs die erste Seite. Ein Klick darauf oder **Vorschau** öffnet ein scrollbares Vorschaufenster: Bilder in voller Breite, PDFs mit bis zu 30 Seiten untereinander. Gescrollt wird mit den Scrollbalken, dem Mausrad (Umschalt + Mausrad seitwärts) oder Bild↑/Bild↓, Pos1/Ende; Esc schließt das Fenster. Die Bilder stammen von Windows (wie im Explorer); PDFs rendert der in Windows 10/11 eingebaute PDF-Renderer. Text-Anhänge lassen sich ebenfalls ansehen, weitere gängige Formate über die zugeordnete Windows-Anwendung öffnen.
- Zum Öffnen erzeugte Kopien von Anhängen löscht die App beim nächsten Start, sobald sie älter als einen Tag sind.

## Einstellungen

Im Reiter **Einstellungen** lässt sich festlegen:

- **Im Infobereich weiterlaufen:** Beim Schließen des Fensters läuft die App mit einem Symbol im Infobereich neben der Uhr weiter. Ein Klick auf das Symbol öffnet das Fenster wieder, Rechtsklick → **Beenden** beendet die App. Standardmäßig eingeschaltet.
- **Mit Windows starten:** Trägt die App für das aktuelle Windows-Benutzerkonto in den Autostart ein. Optional startet sie dabei nur im Hintergrund. Wird die EXE an einen anderen Ort verschoben, bietet der Reiter an, den Autostart anzupassen.
- **Automatischer Abruf:** Aus, alle 10 Minuten, alle 30 Minuten, jede Stunde oder alle 2 Stunden. Abgerufen werden alle Konten mit gespeichertem Passwort, der erste Abruf eine Minute nach dem Programmstart. Während eines Abrufs ist das Symbol orange. Fehler meldet die App in der Statuszeile oder, wenn das Fenster verborgen ist, als Benachrichtigung.
- **Darstellung:** **Design wählen …** (auch oben rechts über **Design …**) bietet 20 Designs, überwiegend dunkel: Nachtblau, Graphit, Anthrazit, Schiefer, Mitternacht, Polarnacht, Tiefsee, Petrol, Waldgrün, Moos, Smaragd, Aubergine, Violette Nacht, Bordeaux, Espresso, Kupfer, Bernstein, Hoher Kontrast, Nebelgrau und Hell. Jedes Design zeigt in der Liste ein Farbmuster; beim Durchblättern stellt sich das ganze Programm sofort darauf um (Vorschau), **Übernehmen** behält es, **Abbrechen** stellt das bisherige wieder her. Alle Designs sind auf gut lesbaren Kontrast von Text, Links und Auswahl geprüft (mindestens 4,5:1). Die HTML-Ansicht einer Mail bleibt so, wie der Absender sie gestaltet hat.
- **Entwicklermodus:** Nach einer Rückfrage lassen sich archivierte Mails, Ordner und Konten aus dem lokalen Archiv löschen. Die App zeigt dann oben rechts „Entwicklermodus“ an. Gelöscht wird per Rechtsklick im Reiter **Archiv**, mit der Entf-Taste oder über **Aus Archiv löschen …**:
  - **Mails:** eine oder mehrere markierte Mails.
  - **Ordner:** die Mails eines Ordners, auf Wunsch mit allen Unterordnern. Die Ordnerauswahl bleibt erhalten.
  - **Konto:** das Konto mit gespeichertem Passwort, Ordnerauswahl, Zeitraum und allen Mails; enthält seine Konto-Datei danach kein Konto mehr, wird die Datei gelöscht (ihr Inhalt wurde vorher überschrieben).

  Auf dem Mailserver wird nichts gelöscht. Liegen gelöschte Mails dort noch und passen sie zu Ordnerauswahl und Zeitraum, archiviert der nächste Abruf sie erneut. Gelöschte Inhalte werden in der Archivdatei überschrieben.

Ein erneuter Start der App öffnet das bereits laufende Fenster, statt eine zweite Instanz zu starten.

## Datenbank

Ab Version 1.18.0 hat jedes Konto eine eigene Datenbankdatei (`Konto-1.sqlite3`, `Konto-2.sqlite3` …) mit seinen Mails, Ordnerauswahl, Zeitraum, Suchindex und dem gespeicherten Passwort. Alle Konto-Dateien liegen zusammen in einem **Archivordner**. Liste, Sortierung und Suche fassen alle Konten wie bisher zusammen („Alle Konten“ im Ordnerbaum).

Der Reiter **Datenbank** zeigt und pflegt den Archivordner:

- **Archivordner:** Pfad des geöffneten Ordners, **Im Explorer zeigen**, **Anderen Archivordner öffnen …** (z. B. auf einem anderen Laufwerk), **Neuen Archivordner anlegen …** (leerer Ordner) und **Standardarchiv verwenden**. Beim Wechsel startet die App neu. Fehlt der gewählte Ordner beim Start (etwa ein nicht angeschlossenes Laufwerk), fragt die App, ob sie stattdessen das Standardarchiv öffnen soll; sie legt dort nie stillschweigend ein leeres Archiv an.
- **Übersicht** und **Konto-Dateien:** Größe, ungenutzter Platz, Anzahl Mails, Zeitraum, Stand des Suchindex, freier Speicher; je Datei Konto, Mails und Größe.
- **Sicherung erstellen …:** Legt am gewählten Ort einen neuen Ordner `SuperNMailArchiv-Sicherung <Datum>` mit einer geprüften Kopie jeder Konto-Datei an. Ein Abbruch oder Fehler hinterlässt keinen halben Sicherungsordner.
- **Sicherung wiederherstellen …:** Ersetzt den Inhalt des Archivordners nach einer Rückfrage durch einen Sicherungsordner – oder durch eine einzelne Archivdatei einer Version bis 1.17, die dabei auf Konto-Dateien aufgeteilt wird. Das geschieht beim Neustart, bevor die App das Archiv benutzt. Die bisherigen Konto-Dateien bleiben im Unterordner `vor-Wiederherstellung <Datum>` erhalten. Gespeicherte Passwörter lassen sich nur unter demselben Windows-Benutzer entschlüsseln.
- **Schnell prüfen / Vollständig prüfen:** Prüft jede Konto-Datei (SQLite `quick_check` bzw. `integrity_check`), die Verweise zwischen Mails und Konten und den Suchindex.
- **Komprimieren:** Schreibt die Konto-Dateien neu und gibt ungenutzten Platz frei (VACUUM), z. B. nach dem Löschen im Entwicklermodus.
- **Suchindex neu aufbauen:** Leert den Suchindex aller Konto-Dateien; die App baut ihn im Hintergrund neu auf.

Während eines Abrufs ist keine Sicherung oder Wartung möglich und umgekehrt; der automatische Abruf wartet. Prüfen, Komprimieren und Sichern lassen sich abbrechen; nicht bearbeitete Dateien bleiben dann unverändert.

### Umstellung auf eine Datei je Konto (1.18.0)

Beim ersten Start von 1.18.0 teilt die App ein vorhandenes Archiv (`archiv.sqlite3`) einmalig mit Fortschrittsanzeige auf. Das Konto mit den meisten Mails wird dabei als ganze Datei kopiert (schnell, Suchindex bleibt erhalten), die übrigen Konten werden mailweise übernommen; deren Suchindex baut die App danach im Hintergrund auf. Jede neue Datei wird geprüft (`quick_check`, gleiche Anzahl Mails je Konto) und erst danach übernommen; anschließend löscht die App die alte `archiv.sqlite3`. Wird die Umstellung unterbrochen, setzt der nächste Start sie fort bzw. beginnt sie neu – das Original bleibt bis zum erfolgreichen Abschluss unverändert. Vorübergehend wird so viel freier Speicher gebraucht, wie das alte Archiv groß ist.

Ältere Programmversionen kennen den Archivordner nicht. Wird über **Vorversion wiederherstellen** auf 1.17 oder älter zurückgegangen, startet diese mit einem leeren Archiv; die Konto-Dateien bleiben unberührt und erscheinen wieder, sobald 1.18 oder neuer läuft.

## Updates

Die Updateroutine stammt aus SuperPyBackup. Die App prüft beim Start und danach alle 12 Stunden, ob es auf GitHub (2xlc-cloud/SuperMailArchiv) eine neuere Release gibt. Abschalten lässt sich das unter **Einstellungen → Updates**. Beim Prüfen wird nie etwas heruntergeladen. Eine neue Version meldet die App einmal je Version in der Statuszeile und, wenn das Fenster verborgen ist, neben der Uhr.

**Update installieren …** lädt die neue Version erst nach Bestätigung herunter und installiert sie nur, wenn alle Prüfungen bestehen:

- Download nur über HTTPS und nur aus Releases des eigenen Repositorys
- Ed25519-Signatur über SHA256SUMS.txt mit dem fest eingebauten öffentlichen Schlüssel – ein manipuliertes Repository oder ein übernommenes GitHub-Konto genügt nicht, um fremde Programme einzuspielen
- Größe und SHA-256 der EXE
- Probelauf der neuen EXE mit `--version`

Danach wird die laufende EXE ersetzt; die bisherige bleibt als `.old`-Datei daneben erhalten (**Vorversion wiederherstellen**). Bei jedem Fehler bleibt die installierte Version unverändert. Liegt die installierte Version mehr als 5 veröffentlichte Versionen zurück, installiert die App das Update als **Pflichtupdate** ohne Rückfrage, mit denselben Prüfungen. Updates gibt es nur für die EXE, nicht beim Start aus dem Quellcode.

## Lokale Daten

Der Archivordner liegt standardmäßig unter %LOCALAPPDATA%\MailArchiv\Konten (anderer Ort: Reiter **Datenbank**), die Einstellungen immer in %LOCALAPPDATA%\MailArchiv\einstellungen.json. Der Datenordner behält seinen bisherigen Namen, damit vorhandene Archive direkt weiterverwendet werden.

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
python -m pip install pyinstaller tkinterweb
python build_exe.py
~~~

Der Build erzeugt die Datei **dist/SuperNMailArchiv.exe** und dieselbe Datei noch einmal als **dist/SuperMailBackup.exe** (alter Name, siehe unten). Die Architektur der EXE entspricht der verwendeten Python-Installation. Daneben entstehen **dist/SHA256SUMS.txt** und, wenn der Signaturschlüssel vorhanden ist, **dist/SHA256SUMS.txt.sig**.

### Release veröffentlichen

Der geheime Signaturschlüssel liegt außerhalb des Repositorys unter `%USERPROFILE%\.supermailbackup\release-signing.key`; nur das eigene Windows-Konto hat Zugriff. Er gehört in eine Datensicherung: Geht er verloren, akzeptieren bestehende Installationen keine neuen Versionen mehr. Ein neuer Schlüssel entsteht nur mit `python tools\sign_release.py keygen`; sein öffentlicher Teil muss dann in `updater.RELEASE_PUBLIC_KEYS` stehen.

Eine Release braucht alle vier Dateien, als Tag die Versionsnummer mit vorangestelltem `v`:

~~~powershell
python build_exe.py
gh release create v1.23.0 dist\SuperNMailArchiv.exe dist\SuperMailBackup.exe dist\SHA256SUMS.txt dist\SHA256SUMS.txt.sig --title "SuperNMailArchiv 1.23.0"
~~~

**Umbenennung (1.23.0):** Bis Version 1.22.0 hieß das Programm SuperMailBackup. Installierte ältere Versionen suchen ihr Update unter dem Namen `SuperMailBackup.exe` und behalten diesen Dateinamen auch danach. Darum enthält jede Release das Programm unter beiden Namen; `SHA256SUMS.txt` führt beide (gleiche Prüfsumme). Ein Autostart-Eintrag unter dem alten Namen wird beim Start auf den neuen Namen umgestellt. Interne Kennungen bleiben absichtlich beim alten Namen: das Fenster für die Einzelinstanz-Prüfung (eine ältere Instanz wird weiter erkannt), die Kopfzeile `X-SuperMailBackup-Resent` und der Ordner `%LOCALAPPDATA%\MailArchiv` mit Archiv und Einstellungen. Der Name des GitHub-Repositorys bleibt `SuperMailArchiv`, weil installierte Versionen Downloads nur von dort annehmen.
