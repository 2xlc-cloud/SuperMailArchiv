"""Maintenance of the archive database: overview, check, compact, rebuild the search index, backup and restore.

Long operations take a progress callback and a cancel event; cancelling rolls back (the file stays as it was).
"""
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from archive import SCHEMA_VERSION, connect

SQLITE_HEADER = b"SQLite format 3\x00"
BACKUP_STEP = 2048  # Pages per backup step (8 MB at 4 KB pages), so progress and cancelling stay responsive.


class Cancelled(Exception):
    pass


def _open(path, readonly=False):
    if readonly:
        db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        return db
    db = connect(path)
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _watch(db, cancel, every=10_000):
    """Let SQLite stop a running statement as soon as cancel is set (raises OperationalError "interrupted")."""
    if cancel is not None:
        db.set_progress_handler(lambda: 1 if cancel.is_set() else 0, every)


def _raise_if_cancelled(cancel, error=None):
    if cancel is not None and cancel.is_set():
        raise Cancelled() from error


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def archive_problem(path):
    """None if path is an archive of this program that this version can open, otherwise the reason (German)."""
    path = Path(path)
    if not path.is_file():
        return "Die Datei existiert nicht."
    try:
        with open(path, "rb") as file:
            header = file.read(16)
    except OSError as error:
        return f"Die Datei kann nicht gelesen werden: {error}"
    if header != SQLITE_HEADER:
        return "Die Datei ist keine SuperMailBackup-Archivdatenbank."
    try:
        with closing(_open(path, readonly=True)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as error:
        return f"Die Datenbank kann nicht geöffnet werden: {error}"
    if not {"mails", "accounts"} <= tables:
        return "Die Datei ist keine SuperMailBackup-Archivdatenbank."
    if version > SCHEMA_VERSION:
        return "Das Archiv stammt von einer neueren Programmversion."
    return None


def archive_summary(path):
    """(number of mails, newest mail date) of an archive file, read without changing it."""
    with closing(_open(path, readonly=True)) as db:
        return tuple(db.execute("SELECT count(*), max(sent) FROM mails").fetchone())


def database_info(path):
    """Overview of the archive file and its contents."""
    path = Path(path)
    with closing(_open(path)) as db:
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        mails, oldest, newest, last_id = db.execute(
            "SELECT count(*), min(sent), max(sent), max(id) FROM mails").fetchone()
        accounts = db.execute("SELECT count(*) FROM accounts WHERE enabled=1").fetchone()[0]
        folders = db.execute("SELECT count(DISTINCT account_id || '/' || folder) FROM mails").fetchone()[0]
        upto = db.execute("SELECT value FROM search_state WHERE name='upto'").fetchone()
        indexed = db.execute("SELECT count(*) FROM mails WHERE id <= ?", (upto[0] if upto else 0,)).fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
    try:
        disk_free = shutil.disk_usage(path.parent).free
    except OSError:
        disk_free = None
    return {"path": str(path), "size": file_size(path), "wal": file_size(f"{path}-wal"),
            "reusable": free * page_size, "page_size": page_size, "pages": pages, "mails": mails,
            "accounts": accounts, "folders": folders, "oldest": oldest, "newest": newest, "indexed": indexed,
            "last_id": last_id, "schema": version, "sqlite": sqlite3.sqlite_version, "disk_free": disk_free}


def check_database(path, full=False, progress=None, cancel=None):
    """Check the archive; returns [(check, ok, detail)]. full=True also verifies indexes against the tables."""
    report = progress or (lambda text: None)
    results = []
    with closing(_open(path)) as db:
        _watch(db, cancel)
        try:
            report("Struktur der Datenbankdatei wird geprüft …")
            rows = [row[0] for row in db.execute(f"PRAGMA {'integrity_check' if full else 'quick_check'}(20)")]
            ok = rows == ["ok"]
            results.append(("Vollständige Prüfung der Datei" if full else "Schnellprüfung der Datei", ok,
                            "Keine Fehler gefunden" if ok else "; ".join(rows[:5])))

            report("Verweise zwischen den Tabellen werden geprüft …")
            broken = db.execute("PRAGMA foreign_key_check").fetchall()
            results.append(("Verweise zwischen Mails und Konten", not broken,
                            "In Ordnung" if not broken else f"{len(broken)} Einträge ohne gültigen Verweis"))

            report("Suchindex wird geprüft …")
            try:
                db.execute("INSERT INTO mail_search(mail_search) VALUES('integrity-check')")
                results.append(("Suchindex: innere Struktur", True, "In Ordnung"))
            except sqlite3.OperationalError as error:
                _raise_if_cancelled(cancel, error)
                results.append(("Suchindex: innere Struktur", False,
                                f"{error} – „Suchindex neu aufbauen“ behebt das"))
            upto = db.execute("SELECT value FROM search_state WHERE name='upto'").fetchone()[0]
            missing = db.execute("SELECT count(*) FROM mails WHERE id > ?", (upto,)).fetchone()[0]
            results.append(("Suchindex: alle Mails erfasst", True,
                            "Vollständig" if not missing else f"{missing} Mails werden noch aufgenommen"))

            pending_spam, pending_clip = db.execute(
                "SELECT count(*) FILTER (WHERE spam_checked IS NULL), "
                "count(*) FILTER (WHERE has_attachments IS NULL) FROM mails").fetchone()
            results.append(("Spam-Bewertung und Anhänge erkannt", True,
                            "Vollständig" if not (pending_spam or pending_clip) else
                            f"Noch offen: {pending_spam} Spam-Bewertungen, {pending_clip} Anhang-Erkennungen "
                            "(läuft im Hintergrund)"))
        except sqlite3.OperationalError as error:
            _raise_if_cancelled(cancel, error)
            raise
    return results


def compact_database(path, progress=None, cancel=None):
    """Rewrite the file without unused space (VACUUM). Returns (size before, size after)."""
    report = progress or (lambda text: None)
    path = Path(path)
    before = file_size(path) + file_size(f"{path}-wal")
    needed = file_size(path) * 2
    try:
        if shutil.disk_usage(path.parent).free < needed:
            raise OSError(f"Zu wenig freier Speicher: Zum Komprimieren werden etwa {needed // 1024 ** 2} MB "
                          "benötigt.")
    except FileNotFoundError:
        pass
    with closing(_open(path)) as db:
        _watch(db, cancel, every=100_000)
        try:
            report("Datei wird neu geschrieben (VACUUM) …")
            db.execute("VACUUM")
            report("Statistiken werden aktualisiert …")
            db.execute("PRAGMA optimize")
            db.execute("INSERT INTO mail_search(mail_search) VALUES('optimize')")
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError as error:
            _raise_if_cancelled(cancel, error)
            raise
    return before, file_size(path) + file_size(f"{path}-wal")


def reset_search_index(path):
    """Empty the search index; the program then rebuilds it in the background (search is slower until then)."""
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO mail_search(mail_search) VALUES('delete-all')")
        db.execute("UPDATE search_state SET value=0 WHERE name='upto'")


def _copy(source_db, target, progress, cancel, label):
    """Online copy (consistent even while the program writes) into target via a temporary file."""
    target = Path(target)
    temporary = target.with_name(target.name + ".teil")
    temporary.unlink(missing_ok=True)

    def step(status, remaining, total):
        if progress and total:
            progress(f"{label}: {100 * (total - remaining) // total} %")
        _raise_if_cancelled(cancel)

    try:
        with closing(sqlite3.connect(temporary)) as copy:
            source_db.backup(copy, pages=BACKUP_STEP, progress=step)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Die Kopie ist fehlerhaft.")
            copy.execute("PRAGMA journal_mode=DELETE")  # One self-contained file, no -wal next to it.
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def backup_database(path, target, progress=None, cancel=None):
    """Write a complete, checked copy of the archive to target. Returns the size of the copy."""
    path, target = Path(path), Path(target)
    if target.resolve() == path.resolve():
        raise OSError("Die Sicherung kann nicht die Archivdatei selbst überschreiben.")
    needed = file_size(path) + file_size(f"{path}-wal")
    if shutil.disk_usage(target.parent).free < needed:
        raise OSError(f"Zu wenig freier Speicher am Ziel: Die Sicherung braucht etwa {needed // 1024 ** 2} MB.")
    with closing(_open(path)) as db:
        _copy(db, target, progress, cancel, "Sicherung")
    return file_size(target)


def default_backup_name(now=None):
    return f"SuperMailBackup-Sicherung {(now or datetime.now()):%Y-%m-%d %H-%M}.sqlite3"


def restore_database(path, source, progress=None):
    """Replace the archive with the backup source. The current archive is kept as a copy next to it first.

    Runs at program start, before the archive is used. Returns the path of that copy.
    """
    path, source = Path(path), Path(source)
    problem = archive_problem(source)
    if problem:
        raise ValueError(problem)
    if source.resolve() == path.resolve():
        raise ValueError("Die Sicherung ist das geöffnete Archiv selbst.")
    with closing(_open(source, readonly=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Die Sicherung ist beschädigt und wurde nicht eingespielt.")
    kept = None
    if path.exists():
        kept = path.with_name(f"{path.stem}-vor-Wiederherstellung {datetime.now():%Y-%m-%d %H-%M-%S}{path.suffix}")
        with closing(_open(path)) as db:
            _copy(db, kept, progress, None, "Bisheriges Archiv wird aufbewahrt")
    # The backup API replaces the whole content in one transaction; an interruption leaves the old archive.
    if progress:
        progress("Sicherung wird eingespielt …")
    with closing(_open(source, readonly=True)) as db, closing(_open(path)) as target:
        db.backup(target, pages=BACKUP_STEP)
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return kept
