"""Maintenance of the archive database: overview, check, compact, rebuild the search index, backup and restore.

Long operations take a progress callback and a cancel event; cancelling rolls back (the file stays as it was).
"""
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from archive import SCHEMA_VERSION, connect, initialize
from archive_set import next_file

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


# --- Since 1.18.0: one file per account --------------------------------------------------------------------------

SPLIT_MARKER = "umstellung.json"
SPLIT_BATCH = 300  # Mails per transaction while splitting (progress and memory stay small).


def split_archive(source, folder, progress=None):
    """Split a single-file archive (up to 1.17) into one file per account in folder.

    All new files are written as *.teil, checked (quick_check, same number of mails per account) and only then
    renamed; then the source is deleted. A marker file lets an interrupted run finish at the next start.
    Returns {account file name: number of mails}.
    """
    report = progress or (lambda done, total, text: None)
    source, folder = Path(source), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / SPLIT_MARKER
    if not marker.exists():
        for stale in folder.glob("*.teil*"):
            stale.unlink()
        needed = file_size(source) + file_size(f"{source}-wal")
        if shutil.disk_usage(folder).free < needed * 1.1 + 100 * 1024 ** 2:
            raise OSError(f"Zu wenig freier Speicher: Die Umstellung braucht vorübergehend etwa "
                          f"{needed // 1024 ** 2} MB zusätzlich.")
        with closing(_open(source)) as db:
            accounts = [row[0] for row in db.execute(
                "SELECT id FROM accounts a WHERE enabled=1 OR EXISTS(SELECT 1 FROM mails WHERE account_id=a.id) "
                "OR EXISTS(SELECT 1 FROM folders WHERE account_id=a.id) ORDER BY id")]
            counts = dict(db.execute("SELECT account_id, count(*) FROM mails GROUP BY account_id").fetchall())
        total = sum(counts.get(account_id, 0) for account_id in accounts)
        # Reading whole mails row by row jumps through the file and can be very slow on some disks; the account
        # with the most mails is therefore copied as a whole file (sequential) and the others are removed from it.
        largest = max(accounts, key=lambda account_id: counts.get(account_id, 0), default=None)
        done = 0
        plan = {}
        for account_id in accounts:
            target = next_file(folder)
            part = target.with_name(target.name + ".teil")
            if account_id == largest:
                mails = counts.get(account_id, 0)
                _copy_whole(source, part, lambda share: report(
                    done + int(mails * share), total,
                    f"Mails werden umgestellt: {done + int(mails * share):,} von {total:,}".replace(",", ".")))
                report(0, 0, f"{target.name}: Mails anderer Konten werden entfernt und überschrieben …")
                _keep_only(part, account_id)
                done += mails
            else:
                initialize(part)
            with closing(_open(part)) as db:
                if account_id == largest:
                    report(0, 0, f"{target.name} wird geprüft (bei großen Archiven einige Minuten) …")
                    _check_split(db, target, counts.get(account_id, 0))
                    plan[part.name] = (target.name, counts.get(account_id, 0))
                    continue
                db.execute("ATTACH DATABASE ? AS old", (str(source),))
                for table in ("accounts", "folders", "account_options", "mails"):
                    mine = [row[1] for row in db.execute(f"PRAGMA main.table_info({table})")]
                    theirs = {row[1] for row in db.execute(f"PRAGMA old.table_info({table})")}
                    columns = ", ".join(column for column in mine if column in theirs)
                    where = "id=?" if table == "accounts" else "account_id=?"
                    if table != "mails":
                        with db:
                            db.execute(f"INSERT INTO main.{table}({columns}) SELECT {columns} FROM old.{table} "
                                       f"WHERE {where}", (account_id,))
                        continue
                    last = 0
                    while True:
                        with db:
                            copied = db.execute(
                                f"INSERT INTO main.mails({columns}) SELECT {columns} FROM old.mails "
                                f"WHERE account_id=? AND id>? ORDER BY id LIMIT ?",
                                (account_id, last, SPLIT_BATCH)).rowcount
                        if copied <= 0:
                            break
                        last = db.execute("SELECT max(id) FROM main.mails").fetchone()[0]
                        done += copied
                        report(done, total, f"Mails werden umgestellt: {done:,} von {total:,}".replace(",", "."))
                db.execute("DETACH DATABASE old")
                report(0, 0, f"{target.name} wird geprüft …")
                _check_split(db, target, counts.get(account_id, 0))
            plan[part.name] = (target.name, counts.get(account_id, 0))
        marker.write_text(json.dumps({"source": str(source), "files": plan}), encoding="utf-8")
    plan = json.loads(marker.read_text(encoding="utf-8"))["files"]
    for part, (target, _) in plan.items():
        if (folder / part).exists():
            for suffix in ("-wal", "-shm"):
                (folder / (part + suffix)).unlink(missing_ok=True)
            os.replace(folder / part, folder / target)
    try:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{source}{suffix}").unlink(missing_ok=True)
    except OSError:
        pass  # Still in use: the new files are complete; the marker makes the next start delete it.
    else:
        marker.unlink()
    return {target: count for target, count in plan.values()}


def _copy_whole(source, part, progress):
    """Page-by-page copy of the whole archive (sequential reading); progress(share 0…1)."""
    part.unlink(missing_ok=True)
    with closing(_open(source)) as db, closing(sqlite3.connect(part)) as copy:
        db.backup(copy, pages=BACKUP_STEP,
                  progress=lambda status, remaining, total: progress((total - remaining) / total if total else 1))
    initialize(part)  # WAL mode and any table or index this version adds.


def _keep_only(part, account_id):
    """Remove all other accounts (with their mails and search index entries) from a whole-file copy.

    Their content is overwritten (secure_delete) and the search index is merged, so no trace of the other accounts'
    mails stays in this file.
    """
    with closing(_open(part)) as db:
        db.execute("PRAGMA secure_delete=ON")
        _delete_other_accounts(db, account_id)
        db.execute("INSERT INTO mail_search(mail_search) VALUES('optimize')")
        db.commit()


def _delete_other_accounts(db, account_id):
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM mail_search WHERE rowid IN (SELECT id FROM mails WHERE account_id != ?)",
                   (account_id,))
        db.execute("DELETE FROM mails WHERE account_id != ?", (account_id,))
        for table in ("folders", "account_options"):
            db.execute(f"DELETE FROM {table} WHERE account_id != ?", (account_id,))
        db.execute("DELETE FROM accounts WHERE id != ?", (account_id,))


def _check_split(db, target, expected):
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise sqlite3.DatabaseError(f"Die neue Datei {target.name} ist fehlerhaft.")
    copied = db.execute("SELECT count(*) FROM mails").fetchone()[0]
    if copied != expected:
        raise sqlite3.DatabaseError(f"{target.name}: {copied} statt {expected} Mails.")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def split_pending(source, folder):
    """True if a single-file archive still has to be split into folder (or an interrupted split finished)."""
    return Path(source).is_file() or (Path(folder) / SPLIT_MARKER).exists()


def default_backup_folder_name(now=None):
    return f"SuperMailBackup-Sicherung {(now or datetime.now()):%Y-%m-%d %H-%M}"


def backup_archive(files, target, progress=None, cancel=None):
    """Checked copies of all account files into the new folder target. Returns the total size."""
    target = Path(target)
    needed = sum(file_size(path) + file_size(f"{path}-wal") for path in files)
    target.mkdir(parents=True, exist_ok=False)
    try:
        if shutil.disk_usage(target).free < needed:
            raise OSError(f"Zu wenig freier Speicher am Ziel: Die Sicherung braucht etwa {needed // 1024 ** 2} MB.")
        for number, path in enumerate(files, 1):
            with closing(_open(path)) as db:
                _copy(db, target / Path(path).name,
                      lambda text: progress(f"Datei {number} von {len(files)} · {text}") if progress else None,
                      cancel, "Sicherung")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)  # No half backup folder.
        raise
    return sum(file_size(path) for path in target.iterdir())


def backup_problem(source):
    """None if source is a backup folder (or an archive file of an older version) that can be restored."""
    from archive_set import account_files

    source = Path(source)
    if source.is_dir():
        files = account_files(source)
        if not files:
            return "Der Ordner enthält keine Konto-Dateien (Konto-1.sqlite3 …)."
        for path in files:
            problem = archive_problem(path)
            if problem:
                return f"{path.name}: {problem}"
        return None
    return archive_problem(source)


def restore_archive(folder, source, progress=None):
    """Replace the account files in folder by a backup folder or an older single-file archive.

    The current files are moved into a subfolder "vor-Wiederherstellung <date>" first. Runs at program start.
    Returns that subfolder (or None if there was nothing to keep).
    """
    from archive_set import account_files

    report = progress or (lambda done, total, text: None)
    folder, source = Path(folder), Path(source)
    problem = backup_problem(source)
    if problem:
        raise ValueError(problem)
    sources = account_files(source) if source.is_dir() else [source]
    for path in sources:
        with closing(_open(path, readonly=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError(f"{path.name} ist beschädigt; es wurde nichts eingespielt.")
    current = account_files(folder)
    kept = None
    if current:
        kept = folder / f"vor-Wiederherstellung {datetime.now():%Y-%m-%d %H-%M-%S}"
        kept.mkdir()
        for path in current:
            for suffix in ("", "-wal", "-shm"):
                if Path(f"{path}{suffix}").exists():
                    os.replace(f"{path}{suffix}", kept / f"{path.name}{suffix}")
    folder.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        for number, path in enumerate(sources, 1):
            report(number - 1, len(sources), f"Datei {number} von {len(sources)} wird eingespielt …")
            with closing(_open(path, readonly=True)) as db:
                _copy(db, folder / path.name, None, None, "Wiederherstellung")
    else:
        # An archive of version 1.17 or older: split a copy, the backup itself stays untouched.
        copy = folder / "wiederherstellung.tmp.sqlite3"
        with closing(_open(source, readonly=True)) as db:
            _copy(db, copy, None, None, "Wiederherstellung")
        split_archive(copy, folder, progress)
    return kept
