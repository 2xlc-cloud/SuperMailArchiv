import imaplib
import json
import re
import sqlite3
import ssl
import unicodedata
from collections import namedtuple
from contextlib import closing
from dataclasses import astuple, dataclass
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from uuid import uuid4

from protection import seal, unseal


@dataclass(frozen=True)
class Account:
    host: str
    port: int
    user: str

    @property
    def label(self):
        return f"{self.user} · {self.host}:{self.port}"


@dataclass(frozen=True)
class Attachment:
    name: str
    part: object

    @property
    def content_type(self):
        return self.part.get_content_type()

    def read(self):
        if self.part.is_multipart():
            if self.content_type == "message/rfc822":
                return self.part.get_payload(0).as_bytes(policy=policy.SMTP)
            return self.part.as_bytes(policy=policy.SMTP)
        return self.part.get_payload(decode=True) or b""


def has_attachments(raw):
    """1 if the mail has attachments (as the Anhänge tab counts them), else 0; None if unreadable."""
    try:
        return int(bool(attachments_from(raw)))
    except Exception:
        return None


def unknown_attachment_flags(path, limit=200, mail_ids=None):
    """Mails whose attachment flag is still unknown: [(id, raw)], newest first, at most limit.

    With mail_ids only those mails are considered (e.g. the ones visible in the list).
    """
    with closing(connect(path)) as db:
        if mail_ids is not None:
            marks = ",".join("?" * len(mail_ids))
            return db.execute(f"SELECT id, raw FROM mails WHERE has_attachments IS NULL AND id IN ({marks})",
                              tuple(mail_ids)).fetchall() if mail_ids else []
        return db.execute("SELECT id, raw FROM mails WHERE has_attachments IS NULL ORDER BY id DESC LIMIT ?",
                          (limit,)).fetchall()


# --- Spam rating of the mail server (1.15.0) -----------------------------------------------------------------
# Only the topmost header of each kind is used: the receiving server adds its own on top, while headers further
# down may come from the sender and could be forged.
SPAMD_RESULT = re.compile(rb"^X-Spamd-Result:\s*\w+:\s*(\w+)\s*\[\s*(-?[\d.]+)\s*/\s*(-?[\d.]+)\s*\](.*)$",
                          re.I | re.M)
SPAM_STATUS = re.compile(rb"^X-Spam-Status:\s*(\w+)\b(?:.*?\bscore=(-?[\d.]+))?", re.I | re.M)
SPAM_SCORE = re.compile(rb"^X-(?:Spam|Rspamd)-Score:\s*(-?[\d.]+)", re.I | re.M)
SPAM_FLAG = re.compile(rb"^X-Spam-Flag:\s*(\w+)", re.I | re.M)
MS_SCL = re.compile(rb"^X-MS-Exchange-Organization-SCL:\s*(-?\d+)", re.I | re.M)
AUTH_RESULTS = re.compile(rb"^Authentication-Results:(.*)$", re.I | re.M)
SYMBOL = re.compile(rb"([A-Z][A-Z0-9_]+)\((-?[\d.]+)\)")


def _headers(raw):
    """Unfolded header block of a mail (at most the first 64 KB)."""
    head = raw[:65536]
    end = min((head.find(mark) for mark in (b"\r\n\r\n", b"\n\n") if mark in head), default=len(head))
    return re.sub(rb"\r?\n[ \t]+", b" ", head[:end])


def spam_info(raw):
    """Spam rating from the server's headers: {score, flag, dmarc_fail, reasons}; score None if not rated."""
    head = _headers(raw)
    score, flag, reasons = None, False, []
    match = SPAMD_RESULT.search(head)
    if match:
        score = float(match[2])
        flag = match[1].lower() == b"true"
        symbols = [(name.decode(), float(weight)) for name, weight in SYMBOL.findall(match[4])]
        reasons = [name for name, weight in sorted(symbols, key=lambda item: -item[1]) if weight > 0][:5]
    else:
        status = SPAM_STATUS.search(head)
        if status:
            flag = status[1].lower() == b"yes"
            score = float(status[2]) if status[2] else None
        if score is None and (direct := SPAM_SCORE.search(head)):
            score = float(direct[1])
    if (marked := SPAM_FLAG.search(head)) and marked[1].lower() == b"yes":
        flag = True
    if (scl := MS_SCL.search(head)) and int(scl[1]) >= 5:
        flag = True
        reasons.append(f"Microsoft SCL {int(scl[1])}")
    auth = AUTH_RESULTS.search(head)
    dmarc_fail = bool(auth and re.search(rb"\bdmarc=fail\b", auth[1], re.I))
    return {"score": score, "flag": flag, "dmarc_fail": dmarc_fail, "reasons": reasons}


def _spam_columns(raw):
    try:
        info = spam_info(raw)
    except Exception:
        return None, 0, 0, 1
    return info["score"], int(info["flag"]), int(info["dmarc_fail"]), 1


def unknown_spam(path, limit=500, mail_ids=None):
    """Mails whose spam rating was not yet read: [(id, start of the raw mail)], newest first."""
    with closing(connect(path)) as db:
        if mail_ids is not None:
            marks = ",".join("?" * len(mail_ids))
            return db.execute(f"SELECT id, substr(raw, 1, 65536) FROM mails WHERE spam_checked IS NULL "
                              f"AND id IN ({marks})", tuple(mail_ids)).fetchall() if mail_ids else []
        return db.execute("SELECT id, substr(raw, 1, 65536) FROM mails WHERE spam_checked IS NULL "
                          "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def store_spam(path, rated):
    """rated: {mail id: (score, flag, dmarc_fail, 1)}."""
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany("UPDATE mails SET spam_score=?, spam_flag=?, dmarc_fail=?, spam_checked=? WHERE id=?",
                       [(*values, mail_id) for mail_id, values in rated.items()])


def spam_progress(path):
    with closing(connect(path)) as db:
        return tuple(db.execute("SELECT count(spam_checked), count(*) FROM mails").fetchone())


def suspicious_sql(threshold):
    """SQL condition for mails the server rated as suspicious."""
    return (f"(coalesce(spam_score, -1000) >= {float(threshold)} OR coalesce(spam_flag, 0) = 1 "
            "OR coalesce(dmarc_fail, 0) = 1)")


def attachment_progress(path):
    """(mails checked, all mails) for the paperclip detection."""
    with closing(connect(path)) as db:
        known, total = db.execute("SELECT count(has_attachments), count(*) FROM mails").fetchone()
    return known, total


def store_attachment_flags(path, flags):
    """flags: {mail id: 0/1}; unreadable mails are stored as 0 so they are not checked again."""
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany("UPDATE mails SET has_attachments=? WHERE id=?",
                       [(0 if flag is None else flag, mail_id) for mail_id, flag in flags.items()])


def attachments_from(raw):
    message = BytesParser(policy=policy.default).parsebytes(raw)

    def collect(part):
        name = part.get_filename()
        if name or part.get_content_disposition() == "attachment":
            suffix = ".eml" if part.get_content_type() == "message/rfc822" else ""
            yield Attachment(name or f"Anhang{suffix}", part)
        elif part.is_multipart():
            for child in part.iter_parts():
                yield from collect(child)

    return list(collect(message))


# Links the app offers to open; everything else (javascript:, file:, …) stays plain text.
LINK_SCHEMES = re.compile(r"(?:https?://|mailto:)", re.I)


class TextFromHTML(HTMLParser):
    def __init__(self, links=False):
        super().__init__()
        self.parts = []
        self.hidden = 0
        self.links = links
        self.anchor = None  # (href, index of the first text part of the link)

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
        elif not self.hidden and tag == "a" and self.links:
            href = (dict(attrs).get("href") or "").strip()
            self.anchor = (href, len(self.parts)) if LINK_SCHEMES.match(href) else None

    def handle_endtag(self, tag):
        if self.hidden and tag in {"script", "style", "head"}:
            self.hidden -= 1
        elif not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")
        elif not self.hidden and tag == "a" and self.anchor:
            # Show where a link really leads: "Text <https://…>" unless the text already is the address.
            href, start = self.anchor
            label = "".join(self.parts[start:]).strip()
            if href.removeprefix("mailto:") not in label:
                self.parts.append(f" <{href}>")
            self.anchor = None

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self):
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def parse_mail(raw, received):
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    def header(name):
        return str(msg.get(name, "")).encode("utf-8", "replace").decode("utf-8")

    try:
        date = parsedate_to_datetime(header("Date"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        timestamp = int(date.timestamp())
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        timestamp = received

    return header("From"), header("To"), header("Subject"), timestamp, _body_text(msg)


def _body_text(msg, links=False):
    body = ""
    for kind in ("plain", "html"):
        part = msg.get_body(preferencelist=(kind,))
        if part is None:
            continue
        try:
            body = part.get_content().strip()
        except (LookupError, UnicodeError, KeyError):
            body = (part.get_payload(decode=True) or b"").decode("utf-8", "replace").strip()
        if kind == "html":
            parser = TextFromHTML(links)
            parser.feed(body)
            parser.close()
            body = parser.text()
        if body:
            break
    return body


def display_text(raw):
    """Message text for the preview; link targets of HTML mails stay visible. None if unreadable."""
    try:
        return _body_text(BytesParser(policy=policy.default).parsebytes(raw), links=True)
    except Exception:
        return None


def safe_parse_mail(raw, received):
    # Malformed mails are still archived; the original stays available as EML.
    try:
        return parse_mail(raw, received)
    except Exception:
        return "", "", "(Mail konnte nicht gelesen werden)", received, ""


def sort_key(text):
    """Alphabetical order as in a German dictionary: Ä like A, ß like ss, case ignored."""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def connect(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    db.create_function("fold", 1, str.casefold, deterministic=True)
    db.create_function("sortkey", 1, sort_key, deterministic=True)
    db.create_function("address", 1, lambda sender: sender_address(sender).casefold(), deterministic=True)
    return db


# Account records before version 4 also contained the IMAP folder.
LegacyAccount = namedtuple("LegacyAccount", "host port user folder")
SCHEMA_VERSION = 4


def _create_schema_v2(db):
    db.execute("""
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY, protected BLOB NOT NULL,
            enabled INTEGER NOT NULL CHECK(enabled IN (0, 1))
        )
    """)
    db.execute("""
        CREATE TABLE mails (
            id INTEGER PRIMARY KEY, mail_id TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            validity INTEGER NOT NULL, uid INTEGER NOT NULL,
            sender TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
            sent INTEGER NOT NULL, body TEXT NOT NULL, raw BLOB NOT NULL,
            UNIQUE(account_id, validity, uid)
        )
    """)
    db.execute("CREATE INDEX mail_date_v2 ON mails(sent DESC, id DESC)")


def _protect(fields, password=None):
    return seal(json.dumps([list(fields), password], ensure_ascii=False).encode("utf-8"))


def _protect_account(account, password=None):
    return _protect(astuple(account), password)


def _records(db):
    for row in db.execute("SELECT id,protected,enabled FROM accounts ORDER BY id"):
        try:
            fields, password = json.loads(unseal(row["protected"]))
        except (OSError, ValueError, TypeError) as error:
            raise ValueError(
                "Kontodaten lassen sich nicht entschlüsseln. Öffne das Archiv mit dem ursprünglichen Windows-Konto."
            ) from error
        yield row["id"], fields, password, bool(row["enabled"])


def _accounts(db):
    for key, fields, password, enabled in _records(db):
        yield key, Account(*fields), password, enabled


def _insert_account(db, account, enabled=True, password=None):
    return db.execute("INSERT INTO accounts(protected,enabled) VALUES (?,?)",
                      (_protect_account(account, password), enabled)).lastrowid


def _migrate(db, legacy_path, version):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "mails" in tables:
        db.execute("ALTER TABLE mails RENAME TO old_mails")
    if "accounts" in tables:
        db.execute("ALTER TABLE accounts RENAME TO old_accounts")
    _create_schema_v2(db)
    fields = "host,port,username,folder"
    enabled = set()
    if "accounts" in tables:
        enabled.update(LegacyAccount(*row) for row in db.execute(f"SELECT {fields} FROM old_accounts"))
    if version == 0:
        try:
            old = json.loads(legacy_path.read_text(encoding="utf-8"))
            enabled.add(LegacyAccount(old["host"], int(old["port"]), old["user"], old["folder"]))
        except FileNotFoundError:
            pass
    archived = set()
    if "mails" in tables:
        archived.update(LegacyAccount(*row) for row in db.execute(f"SELECT DISTINCT {fields} FROM old_mails"))
    db.create_function("new_mail_id", 0, lambda: str(uuid4()))
    for account in enabled | archived:
        key = db.execute("INSERT INTO accounts(protected,enabled) VALUES (?,?)",
                         (_protect(account), account in enabled)).lastrowid
        if account in archived:
            db.execute("""
                INSERT INTO mails(id,mail_id,account_id,validity,uid,sender,recipient,subject,sent,body,raw)
                SELECT id,new_mail_id(),?,validity,uid,sender,recipient,subject,sent,body,raw
                FROM old_mails WHERE host=? AND port=? AND username=? AND folder=?
            """, (key, *account))
    if "mails" in tables:
        db.execute("DROP TABLE old_mails")
    if "accounts" in tables:
        db.execute("DROP TABLE old_accounts")
    db.execute("PRAGMA user_version=2")


def _upgrade_folders(db):
    """Version 4: one account per server login; the IMAP folder is stored with each mail."""
    records = list(_records(db))
    groups = {}
    for key, fields, password, enabled in records:
        legacy = LegacyAccount(*fields)
        groups.setdefault(Account(legacy.host, legacy.port, legacy.user), []).append(
            (key, legacy.folder, password, enabled))
    db.execute("CREATE TEMP TABLE account_map (old INTEGER PRIMARY KEY, new INTEGER NOT NULL, folder TEXT NOT NULL)")
    for account, members in groups.items():
        # Keep an enabled record if there is one, so its ID and saved password survive.
        members.sort(key=lambda member: (not member[3], member[0]))
        target = members[0][0]
        password = next((member[2] for member in members if member[3] and member[2]), None)
        enabled = any(member[3] for member in members)
        db.execute("UPDATE accounts SET protected=?, enabled=? WHERE id=?",
                   (_protect_account(account, password), enabled, target))
        db.executemany("INSERT INTO account_map VALUES (?,?,?)",
                       [(key, target, folder) for key, folder, _, _ in members])
    db.execute("""
        CREATE TABLE mails_v4 (
            id INTEGER PRIMARY KEY, mail_id TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL REFERENCES accounts(id), folder TEXT NOT NULL,
            validity INTEGER NOT NULL, uid INTEGER NOT NULL,
            sender TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
            sent INTEGER NOT NULL, body TEXT NOT NULL, raw BLOB NOT NULL,
            UNIQUE(account_id, folder, validity, uid)
        )
    """)
    db.execute("""
        INSERT INTO mails_v4(id,mail_id,account_id,folder,validity,uid,sender,recipient,subject,sent,body,raw)
        SELECT m.id,m.mail_id,map.new,map.folder,m.validity,m.uid,m.sender,m.recipient,m.subject,m.sent,m.body,m.raw
        FROM mails m JOIN account_map map ON map.old = m.account_id
    """)
    db.execute("DROP TABLE mails")
    db.execute("ALTER TABLE mails_v4 RENAME TO mails")
    db.execute("CREATE INDEX mail_date_v4 ON mails(sent DESC, id DESC)")
    db.execute("CREATE INDEX mail_folder_v4 ON mails(account_id, folder)")
    db.execute("DELETE FROM accounts WHERE id IN (SELECT old FROM account_map WHERE old != new)")
    db.execute("""
        CREATE TABLE folders (
            account_id INTEGER NOT NULL REFERENCES accounts(id), name TEXT NOT NULL,
            delimiter TEXT, role TEXT,
            PRIMARY KEY(account_id, name)
        )
    """)
    _add_folder_selection(db)
    db.execute("""
        INSERT OR IGNORE INTO folders(account_id, name, role)
        SELECT DISTINCT new, folder, CASE WHEN folder='INBOX' THEN 'inbox' END FROM account_map
    """)
    db.execute("DROP TABLE account_map")


def _add_folder_selection(db):
    """Folder choice per account (1.4.0). Extra columns keep the archive readable for 1.3.0."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(folders)")}
    if "default_on" not in columns:
        # Whether the folder is archived unless the user decided otherwise (off for spam, trash, …).
        db.execute("ALTER TABLE folders ADD COLUMN default_on INTEGER NOT NULL DEFAULT 1")
    if "selected" not in columns:
        db.execute("ALTER TABLE folders ADD COLUMN selected INTEGER")  # NULL: use default_on
    if "server_count" not in columns:
        db.execute("ALTER TABLE folders ADD COLUMN server_count INTEGER")  # Mails on the server (1.7.0)
    if "server_deleted" not in columns:
        db.execute("ALTER TABLE folders ADD COLUMN server_deleted INTEGER")  # Of these marked \Deleted (1.8.0)


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path = path.with_name("konto.json")
    with closing(connect(path)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("Die Archivdatenbank benötigt eine neuere Programmversion.")
        if version < 2:
            _migrate(db, legacy_path, version)
            version = 2
        db.commit()
        if version == 2:
            # Rewrite freed pages, then remove old WAL frames and the old settings file.
            db.execute("VACUUM")
            if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                raise OSError("Schließe weitere Fenster der App, damit die Umstellung abgeschlossen werden kann.")
            legacy_path.unlink(missing_ok=True)
            db.execute("PRAGMA user_version=3")
            version = 3
        if version == 3:
            if db.execute("SELECT EXISTS(SELECT 1 FROM mails)").fetchone()[0]:
                # Older program versions cannot open the new format; keep a copy for them.
                backup = path.with_name("archiv-vor-1.3.sqlite3")
                if not backup.exists():
                    with closing(sqlite3.connect(backup)) as copy:
                        db.backup(copy)
            db.execute("BEGIN IMMEDIATE")
            _upgrade_folders(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        db.execute("BEGIN IMMEDIATE")
        _add_folder_selection(db)
        # Archive period per account (1.5.0); a separate table keeps older versions compatible.
        db.execute("""CREATE TABLE IF NOT EXISTS account_options (
                          account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
                          date_from TEXT NOT NULL DEFAULT '', date_to TEXT NOT NULL DEFAULT '')""")
        if "skip_deleted" not in {row[1] for row in db.execute("PRAGMA table_info(account_options)")}:
            # 1.8.0: leave out mails the mail program only marked as deleted (\Deleted, not yet expunged).
            db.execute("ALTER TABLE account_options ADD COLUMN skip_deleted INTEGER NOT NULL DEFAULT 1")
        if "has_attachments" not in {row[1] for row in db.execute("PRAGMA table_info(mails)")}:
            # 1.12.0: paperclip in the mail list. NULL = not yet determined (older mails are checked later).
            db.execute("ALTER TABLE mails ADD COLUMN has_attachments INTEGER")
        columns = {row[1] for row in db.execute("PRAGMA table_info(mails)")}
        for column, kind in (("spam_score", "REAL"), ("spam_flag", "INTEGER"), ("dmarc_fail", "INTEGER"),
                             ("spam_checked", "INTEGER")):
            if column not in columns:
                # 1.15.0: spam rating of the mail server; spam_checked NULL = not yet read from the headers.
                db.execute(f"ALTER TABLE mails ADD COLUMN {column} {kind}")
        _create_search_index(db)
        db.commit()


# Archive period: '' (no limit), 'today', 'yesterday', 'week' (7 days ago) or 'YYYY-MM' (a month).
RELATIVE_DAYS = {"today": 0, "yesterday": 1, "week": 7}
IMAP_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _period_bound(spec, today, end):
    if not spec:
        return None
    if spec in RELATIVE_DAYS:
        return today - timedelta(days=RELATIVE_DAYS[spec])
    match = re.fullmatch(r"(\d{4})-(\d{2})", spec)
    if not match or not 1 <= int(match[2]) <= 12 or not 1900 <= int(match[1]) <= 9999:
        raise ValueError("Ungültige Zeitraumangabe.")
    first = date(int(match[1]), int(match[2]), 1)
    if not end:
        return first
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return following - timedelta(days=1)


def resolve_period(date_from, date_to, today=None):
    """First and last day (inclusive) of the archive period; None means no limit."""
    today = today or date.today()
    start, end = _period_bound(date_from, today, False), _period_bound(date_to, today, True)
    if start and end and start > end:
        raise ValueError("Der Beginn des Zeitraums liegt nach dessen Ende.")
    return start, end


def _imap_date(day):
    return f"{day.day}-{IMAP_MONTHS[day.month - 1]}-{day.year}"


def search_criteria(start, end):
    """IMAP SEARCH keys for the period; SINCE/BEFORE compare the server's arrival date."""
    criteria = []
    if start:
        criteria += ["SINCE", _imap_date(start)]
    if end:
        criteria += ["BEFORE", _imap_date(end + timedelta(days=1))]
    return criteria or ["ALL"]


def get_skip_deleted(path, account_id):
    with closing(connect(path)) as db:
        row = db.execute("SELECT skip_deleted FROM account_options WHERE account_id=?", (account_id,)).fetchone()
    return bool(row[0]) if row else True


def set_skip_deleted(path, account_id, skip):
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO account_options(account_id, skip_deleted) VALUES (?,?)
                      ON CONFLICT(account_id) DO UPDATE SET skip_deleted=excluded.skip_deleted""",
                   (account_id, int(skip)))


def get_period(path, account_id):
    with closing(connect(path)) as db:
        row = db.execute("SELECT date_from, date_to FROM account_options WHERE account_id=?",
                         (account_id,)).fetchone()
    return (row[0], row[1]) if row else ("", "")


def set_period(path, account_id, date_from, date_to):
    resolve_period(date_from, date_to)  # Raises ValueError for an invalid period.
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO account_options(account_id, date_from, date_to) VALUES (?,?,?)
                      ON CONFLICT(account_id) DO UPDATE SET date_from=excluded.date_from,
                      date_to=excluded.date_to""", (account_id, date_from, date_to))


def _deleting(path):
    """Connection for deletions: overwrite freed pages so deleted mails do not remain in the file."""
    db = connect(path)
    db.execute("PRAGMA secure_delete=ON")
    return db


def count_mails(path, account_id=None, folders=None, mail_ids=None):
    where, params = ["1"], []
    if account_id is not None:
        where.append("account_id=?")
        params.append(account_id)
    if folders is not None:
        where.append(f"folder IN ({','.join('?' * len(folders))})")
        params.extend(folders)
    if mail_ids is not None:
        where.append(f"id IN ({','.join('?' * len(mail_ids))})")
        params.extend(mail_ids)
    with closing(connect(path)) as db:
        return db.execute(f"SELECT count(*) FROM mails WHERE {' AND '.join(where)}", params).fetchone()[0]


def _finish_delete(db):
    # Fold the write-ahead log into the file and empty it, so no copy of deleted pages stays behind.
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def delete_mails(path, mail_ids):
    """Delete archived mails from the local database only. Returns the number deleted."""
    with closing(_deleting(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany("DELETE FROM mail_search WHERE rowid=?", [(mail_id,) for mail_id in mail_ids])
            deleted = db.executemany("DELETE FROM mails WHERE id=?", [(mail_id,) for mail_id in mail_ids]).rowcount
        _finish_delete(db)
    return deleted


def delete_folder_mails(path, account_id, folders):
    """Delete all archived mails of the given folders locally; the folder choice stays as it is."""
    with closing(_deleting(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            for folder in folders:
                _remove_from_index(db, "account_id=? AND folder=?", (account_id, folder))
            deleted = db.executemany("DELETE FROM mails WHERE account_id=? AND folder=?",
                                     [(account_id, folder) for folder in folders]).rowcount
        _finish_delete(db)
    return deleted


def delete_account(path, account_id):
    """Delete an account with its mails, folders, period and saved password from the local database."""
    with closing(_deleting(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            _remove_from_index(db, "account_id=?", (account_id,))
            deleted = db.execute("DELETE FROM mails WHERE account_id=?", (account_id,)).rowcount
            for table in ("folders", "account_options"):
                db.execute(f"DELETE FROM {table} WHERE account_id=?", (account_id,))
            db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        _finish_delete(db)
    return deleted


def saved_accounts(path):
    with closing(connect(path)) as db:
        accounts = [(key, account) for key, account, _, enabled in _accounts(db) if enabled]
    return dict(sorted(accounts, key=lambda item: item[1].label.casefold()))


ROLE_ORDER = {"inbox": 0, "drafts": 1, "sent": 2, "archive": 3, "junk": 11, "trash": 12, "all": 13, "flagged": 14}


def _folder_order(folder):
    return ROLE_ORDER.get(folder[2], 9), folder[0].casefold()


def folder_tree(path):
    """Accounts with archived mails or enabled, each with its folders: (name, delimiter, role, count).

    Folders appear when they are chosen for archiving or already contain mails.
    """
    with closing(connect(path)) as db:
        counts = {}
        for row in db.execute("SELECT account_id, folder, count(*) FROM mails GROUP BY account_id, folder"):
            counts[(row[0], row[1])] = row[2]
        folders = {}
        for row in db.execute("SELECT account_id, name, delimiter, role, coalesce(selected, default_on) FROM folders"):
            count = counts.get((row[0], row[1]), 0)
            if row[4] or count:
                folders.setdefault(row[0], []).append((row[1], row[2], row[3], count))
        for (key, name), count in counts.items():
            if not any(folder[0] == name for folder in folders.get(key, [])):
                folders.setdefault(key, []).append((name, None, "inbox" if name == "INBOX" else None, count))
        tree = []
        for key, account, _, enabled in _accounts(db):
            if enabled or key in folders:
                tree.append((key, account, sorted(folders.get(key, []), key=_folder_order)))
    return sorted(tree, key=lambda item: item[1].label.casefold())


def account_folders(path, account_id):
    """All known folders of one account.

    Returns (name, delimiter, role, archived?, archived mails, mails on the server, of these marked as
    deleted); the server numbers are None while unknown.
    """
    with closing(connect(path)) as db:
        counts = dict(db.execute("SELECT folder, count(*) FROM mails WHERE account_id=? GROUP BY folder",
                                 (account_id,)).fetchall())
        rows = db.execute("SELECT name, delimiter, role, coalesce(selected, default_on), server_count, "
                          "server_deleted FROM folders "
                          "WHERE account_id=?", (account_id,)).fetchall()
    folders = [(name, delimiter, role, bool(chosen), counts.get(name, 0), on_server, deleted)
               for name, delimiter, role, chosen, on_server, deleted in rows]
    return sorted(folders, key=_folder_order)


def set_folder_selection(path, account_id, names, selected):
    """Choose whether the given folders are archived from now on. Archived mails stay."""
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany("INSERT OR IGNORE INTO folders(account_id, name) VALUES (?,?)",
                       [(account_id, name) for name in names])
        db.executemany("UPDATE folders SET selected=? WHERE account_id=? AND name=?",
                       [(int(selected), account_id, name) for name in names])


def _store_folders(db, account_id, folders):
    with db:
        db.execute("BEGIN IMMEDIATE")
        for name, delimiter, role, default_on in folders:
            db.execute("""INSERT INTO folders(account_id, name, delimiter, role, default_on) VALUES (?,?,?,?,?)
                          ON CONFLICT(account_id, name) DO UPDATE SET delimiter=excluded.delimiter,
                          role=excluded.role, default_on=excluded.default_on""",
                       (account_id, name, delimiter, role, int(default_on)))


def save_account(path, account, account_id=None):
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        accounts = list(_accounts(db))
        match = next((row for row in accounts if row[1] == account), None)
        if match is not None and match[0] == account_id:
            return account_id
        if match is not None and match[3]:
            raise sqlite3.IntegrityError("Konto bereits vorhanden.")
        if account_id is not None:
            _disable_account(db, account_id, accounts)
        if match is None:
            return _insert_account(db, account)
        db.execute("UPDATE accounts SET enabled=1 WHERE id=?", (match[0],))
        return match[0]


def _disable_account(db, key, accounts):
    for account_id, account, _, enabled in accounts:
        if account_id == key and enabled:
            db.execute("UPDATE accounts SET enabled=0, protected=? WHERE id=?",
                       (_protect_account(account), key))
            break


def remove_account(path, account_id):
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _disable_account(db, account_id, list(_accounts(db)))


def account_password(path, account_id):
    with closing(connect(path)) as db:
        return next((password for key, _, password, enabled in _accounts(db)
                     if key == account_id and enabled), None)


def remember_password(path, account_id, account, password):
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for key, saved, _, enabled in _accounts(db):
            if key == account_id and saved == account and enabled:
                db.execute("UPDATE accounts SET protected=? WHERE id=?",
                           (_protect_account(account, password), key))
                break


def _account_key(db, account):
    return next((key for key, saved, _, _ in _accounts(db) if saved == account), None)


# Sort keys of the mail list; "time" sorts by the local time of day, "date" by the full timestamp.
SORT_KEYS = {
    "subject": "sortkey(subject)",
    "sender": "sortkey(sender)",
    "date": "sent",
    "time": "strftime('%H:%M:%S', sent, 'unixepoch', 'localtime')",
    "attach": "coalesce(has_attachments, 0)",
    "size": "length(raw)",
    "spam": "coalesce(spam_score, -1000)",  # Replaced in find_mails: suspicious first (needs the threshold).
}


def sender_address(sender):
    """The e-mail address of a From header such as 'Anna <anna@example.de>'; falls back to the whole text."""
    address = parseaddr(sender or "")[1]
    return (address or sender or "").strip()


# Full-text index (1.13.0). Only the index is stored (contentless); the text stays in the mails table.
# Trigrams find any part of a word like the earlier search ("rechnung" in "Telefonrechnung"), ignoring case
# and accents ("muller" finds "Müller"). Words shorter than three characters cannot use the index.
SEARCH_TEXT = "mail_id || ' ' || subject || ' ' || sender || ' ' || recipient || ' ' || body"


def _create_search_index(db):
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS mail_search USING fts5(
                      text, content='', contentless_delete=1, tokenize='trigram remove_diacritics 1')""")
    # "upto": mails up to this ID are in the index; newer ones are added by build_search_index().
    db.execute("CREATE TABLE IF NOT EXISTS search_state (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    db.execute("INSERT OR IGNORE INTO search_state VALUES ('upto', 0)")


def _indexed_upto(db):
    return db.execute("SELECT value FROM search_state WHERE name='upto'").fetchone()[0]


def _index_new_mail(db, mail_id):
    """Index a mail right after inserting it, if all older mails are indexed already (same transaction)."""
    upto = _indexed_upto(db)
    if not db.execute("SELECT EXISTS(SELECT 1 FROM mails WHERE id > ? AND id != ?)", (upto, mail_id)).fetchone()[0]:
        db.execute(f"INSERT INTO mail_search(rowid, text) SELECT id, {SEARCH_TEXT} FROM mails WHERE id=?",
                   (mail_id,))
        db.execute("UPDATE search_state SET value=max(value, ?) WHERE name='upto'", (mail_id,))


def build_search_index(path, batch=2000):
    """Add up to batch not yet indexed mails to the index. Returns (indexed, all mails); runs in SQLite (C)."""
    with closing(connect(path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            upto = _indexed_upto(db)
            last = db.execute("SELECT max(id) FROM (SELECT id FROM mails WHERE id > ? ORDER BY id LIMIT ?)",
                              (upto, batch)).fetchone()[0]
            if last is not None:
                db.execute(f"INSERT INTO mail_search(rowid, text) SELECT id, {SEARCH_TEXT} FROM mails "
                           "WHERE id > ? AND id <= ?", (upto, last))
                db.execute("UPDATE search_state SET value=? WHERE name='upto'", (last,))
                upto = last
        indexed, total = db.execute("SELECT count(*) FILTER (WHERE id <= ?), count(*) FROM mails",
                                    (upto,)).fetchone()
    return indexed, total


def _remove_from_index(db, condition, params):
    db.execute(f"DELETE FROM mail_search WHERE rowid IN (SELECT id FROM mails WHERE {condition})", params)


def search_words(query):
    """Words of a search; parts without letters or digits are ignored."""
    return [word for word in query.split() if any(char.isalnum() for char in word)]


def fts_query(words):
    """Trigram MATCH expression: every word (at least 3 characters) must occur somewhere."""
    return " ".join('"' + word.replace('"', '""') + '"' for word in words if len(word) >= 3)


def find_mails(path, query, offset=0, limit=100, account_id=None, folder=None, sort="date", descending=True,
               sender=None, only_suspicious=False, threshold=5):
    conditions, params = [], []
    words = search_words(query)
    if words:
        # Indexed mails through the index; mails not yet indexed (index still being built) the slow way, but
        # with SQLite's own lower() so no Python code runs for every mail. Short words are checked the slow way
        # too, but only among the mails the index found.
        def contains(selection):
            return [f"instr(lower({SEARCH_TEXT}), ?) > 0" for _ in selection], [word.lower() for word in selection]

        upto = "(SELECT value FROM search_state WHERE name='upto')"
        match = fts_query(words)
        short_sql, short_params = contains([word for word in words if len(word) < 3])
        indexed = [f"id <= {upto}"] + ([f"id IN (SELECT rowid FROM mail_search WHERE mail_search MATCH ?)"]
                                       if match else []) + short_sql
        slow_sql, slow_params = contains(words)
        conditions.append(f"(({' AND '.join(indexed)}) OR (id > {upto} AND {' AND '.join(slow_sql)}))")
        params += ([match] if match else []) + short_params + slow_params
    if account_id is not None:
        conditions.append("account_id=?")
        params.append(account_id)
        if folder is not None:
            conditions.append("folder=?")
            params.append(folder)
    if sender:
        # Compare the whole address, whatever display name was used ("hanna@" must not match "anna@").
        conditions.append("address(sender) = ?")
        params.append(sender.casefold())
    if only_suspicious:
        conditions.append(suspicious_sql(threshold))
    where = " AND ".join(conditions) or "1"
    direction = "DESC" if descending else "ASC"
    key = SORT_KEYS.get(sort, "sent")
    if sort == "spam":
        key = f"(CASE WHEN {suspicious_sql(threshold)} THEN 1000 ELSE 0 END + coalesce(spam_score, -1000))"
    order = f"{key} {direction}, sent {direction}, id {direction}"
    with closing(connect(path)) as db:
        return db.execute(
            f"SELECT id, sender, subject, sent, has_attachments, length(raw) AS size, spam_score, spam_flag, "
            f"dmarc_fail, spam_checked FROM mails WHERE {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset)
        ).fetchall()


def get_mail(path, mail_id, raw=False):
    with closing(connect(path)) as db:
        if raw:
            return db.execute("SELECT raw FROM mails WHERE id=?", (mail_id,)).fetchone()
        row = db.execute("""
            SELECT sender, recipient, subject, sent, body, mail_id, account_id, folder, length(raw) AS size
            FROM mails WHERE id=?
        """, (mail_id,)).fetchone()
        if row is None:
            return None
        account = next(account for key, account, _, _ in _accounts(db) if key == row["account_id"])
        return {**dict(row), **vars(account)}


def mailbox_argument(folder):
    # IMAP modified UTF-7 for mailbox names.
    def encode(match):
        from base64 import b64encode
        data = b64encode(match.group().encode("utf-16-be")).decode("ascii")
        return "&" + data.rstrip("=").replace("/", ",") + "-"

    folder = re.sub(r"[^\x20-\x7e]+", encode, folder.replace("&", "&-"))
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def decode_mailbox(name):
    """Decode IMAP modified UTF-7 (RFC 3501) into a Unicode folder name."""
    from base64 import b64decode

    def decode(match):
        data = match.group(1)
        if not data:
            return "&"
        data = data.replace(",", "/")
        try:
            return b64decode(data + "=" * (-len(data) % 4)).decode("utf-16-be")
        except (ValueError, UnicodeError):
            return match.group()

    return re.sub(r"&([A-Za-z0-9+,]*)-", decode, name)


LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?P<delimiter>"(?:[^"\\]|\\.)*"|NIL)\s*(?P<name>.*)',
                       re.I | re.S)
UNSELECTABLE_FLAGS = {"\\noselect", "\\nonexistent"}
# Folders that are not archived by default: spam, trash and Gmail's views that repeat other folders.
OFF_FLAGS = {"\\junk": "junk", "\\trash": "trash", "\\all": "all", "\\flagged": "flagged",
             "\\important": "flagged"}
ROLE_FLAGS = {"\\sent": "sent", "\\drafts": "drafts", "\\archive": "archive"}
# Servers without SPECIAL-USE flags: recognise common folder names (last path component, lower case).
OFF_NAMES = {"spam": "junk", "junk": "junk", "junk e-mail": "junk", "junk-e-mail": "junk", "spamverdacht": "junk",
             "trash": "trash", "papierkorb": "trash", "deleted items": "trash", "deleted messages": "trash",
             "gelöschte elemente": "trash", "gelöschte objekte": "trash", "gelöscht": "trash"}
ROLE_NAMES = {"sent": "sent", "sent items": "sent", "sent messages": "sent", "gesendet": "sent",
              "gesendete elemente": "sent", "gesendete objekte": "sent", "drafts": "drafts",
              "entwürfe": "drafts", "archive": "archive", "archiv": "archive"}


def _unquote(value):
    value = value.strip()
    if value.startswith(b'"') and value.endswith(b'"') and len(value) >= 2:
        return re.sub(rb"\\(.)", rb"\1", value[1:-1])
    return value


def parse_folder_list(data):
    """Return (name, delimiter, role, archived by default) for each selectable folder, inbox first."""
    folders = []
    for item in data:
        if isinstance(item, tuple):  # Name sent as literal.
            line, name = item[0], item[1]
        elif isinstance(item, bytes):
            line, name = item, None
        else:
            continue
        match = LIST_LINE.match(line)
        if not match:
            continue
        if name is None:
            name = _unquote(match["name"])
        flags = set(match["flags"].decode("ascii", "replace").lower().split())
        delimiter = None if match["delimiter"].upper() == b"NIL" else _unquote(match["delimiter"]).decode(
            "utf-8", "replace") or None
        name = decode_mailbox(name.decode("utf-8", "replace"))
        if name.upper() == "INBOX":
            name = "INBOX"
        leaf = (name.rsplit(delimiter, 1)[-1] if delimiter else name).casefold()
        if flags & UNSELECTABLE_FLAGS:
            continue
        off = next((OFF_FLAGS[flag] for flag in OFF_FLAGS if flag in flags), None)
        if off is None and not flags & set(ROLE_FLAGS):
            off = OFF_NAMES.get(leaf)
        role = "inbox" if name == "INBOX" else off or next(
            (ROLE_FLAGS[flag] for flag in ROLE_FLAGS if flag in flags), ROLE_NAMES.get(leaf))
        folders.append((name, delimiter, role, name == "INBOX" or off is None))
    unique = {folder[0]: folder for folder in folders}
    return sorted(unique.values(), key=_folder_order)


def clean_password(password):
    # Copying a password can include the line ending of the source field.
    password = password.strip("\r\n")
    if not password:
        raise ValueError("Bitte gib ein Passwort oder App-Passwort ein.")
    if any(c in password for c in "\r\n\0"):
        raise ValueError("Das Passwort enthält einen Zeilenumbruch oder ein Nullzeichen. Kopiere es als eine Zeile.")
    return password


def login(client, user, password):
    try:
        if user.isascii() and password.isascii():
            client.login('"' + user.replace("\\", "\\\\").replace('"', '\\"') + '"', password)
        elif "AUTH=PLAIN" in client.capabilities:
            # LOGIN only accepts ASCII in imaplib; SASL PLAIN transmits UTF-8 (RFC 4616).
            credentials = b"\0" + user.encode("utf-8") + b"\0" + password.encode("utf-8")
            client.authenticate("PLAIN", lambda _: credentials)
        else:
            raise ValueError(
                "Der Server unterstützt keine Anmeldung mit Umlauten oder Sonderzeichen in "
                "Benutzername oder Passwort. Verwende ein App-Passwort ohne solche Zeichen."
            )
    except imaplib.IMAP4.error:
        raise ValueError("Anmeldung fehlgeschlagen. Prüfe Benutzername und (App-)Passwort.") from None


def _connect_imap(account, password):
    password = clean_password(password)
    if any(c in account.user for c in "\r\n\0"):
        raise ValueError("Der Benutzername enthält einen Zeilenumbruch oder ein Nullzeichen.")
    client = imaplib.IMAP4_SSL(account.host, account.port, ssl_context=ssl.create_default_context(), timeout=25)
    try:
        login(client, account.user, password)
    except BaseException:
        client.shutdown()
        raise
    return client


def _refresh_folders(client, db, account):
    """Read the server's folder list, remember it and return the account ID."""
    status, data = client.list()
    if status != "OK" or not data:
        raise ValueError("Der Server hat keine Ordnerliste geliefert.")
    folders = parse_folder_list(data)
    with db:
        db.execute("BEGIN IMMEDIATE")
        account_id = _account_key(db, account)
        if account_id is None:
            account_id = _insert_account(db, account, enabled=False)
    _store_folders(db, account_id, folders)
    _store_server_counts(client, db, account_id, [folder[0] for folder in folders])
    return account_id, [folder[0] for folder in folders]


def _store_server_counts(client, db, account_id, names):
    """Ask the server how many mails each folder holds (IMAP STATUS); unknown counts stay empty."""
    counts = []
    for name in names:
        try:
            status, data = client.status(mailbox_argument(name), "(MESSAGES)")
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            continue
        match = re.search(rb"MESSAGES (\d+)", b" ".join(item for item in data if isinstance(item, bytes)),
                          re.I) if status == "OK" and data else None
        if match:
            counts.append((int(match[1]), account_id, name))
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany("UPDATE folders SET server_count=? WHERE account_id=? AND name=?", counts)


def load_folders(path, account, password):
    """Fetch only the folder list, so folders can be chosen before the first sync."""
    with _connect_imap(account, password) as client, closing(connect(path)) as db:
        return _refresh_folders(client, db, account)[0]


def sync(path, account, password, progress, cancelled):
    """Archive the chosen folders of the account. progress(folder, done, total).

    Returns (saved, missing, failed_folders).
    """
    with _connect_imap(account, password) as client, closing(connect(path)) as db:
        account_id, on_server = _refresh_folders(client, db, account)
        chosen = {row[0] for row in db.execute(
            "SELECT name FROM folders WHERE account_id=? AND coalesce(selected, default_on)", (account_id,))}
        row = db.execute("SELECT date_from, date_to FROM account_options WHERE account_id=?",
                         (account_id,)).fetchone()
        criteria = search_criteria(*resolve_period(*(tuple(row) if row else ("", ""))))
        if get_skip_deleted(path, account_id):
            criteria = [item for item in criteria if item != "ALL"] + ["UNDELETED"]
        saved, missing, failed = 0, 0, []
        for name in on_server:
            if name not in chosen:
                continue
            if cancelled.is_set():
                break
            try:
                result = _sync_folder(client, db, account_id, name, lambda done, total: progress(name, done, total),
                                      cancelled, criteria)
            except imaplib.IMAP4.abort:
                raise
            except (imaplib.IMAP4.error, ValueError):
                failed.append(name)
                continue
            saved, missing = saved + result[0], missing + result[1]
        return saved, missing, failed


def _store_folder_counts(client, db, account_id, folder, exists):
    """Remember how many mails the open folder holds and how many of them are only marked as deleted."""
    total = int(exists[0]) if exists and exists[0] and exists[0].isdigit() else None
    try:
        status, data = client.uid("SEARCH", None, "DELETED")
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        status, data = "NO", []
    deleted = len(data[0].split()) if status == "OK" and data and data[0] is not None else None
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE folders SET server_count=coalesce(?, server_count), server_deleted=? "
                   "WHERE account_id=? AND name=?", (total, deleted, account_id, folder))


def _sync_folder(client, db, account_id, folder, progress, cancelled, criteria=("ALL",)):
    status, exists = client.select(mailbox_argument(folder), readonly=True)
    if status != "OK":
        raise ValueError(f"Der IMAP-Ordner {folder} konnte nicht geöffnet werden.")
    _, info = client.response("UIDVALIDITY")
    if not info or not info[-1] or not info[-1].isdigit() or int(info[-1]) == 0:
        raise ValueError("Der Server hat keine gültige UIDVALIDITY geliefert.")
    key = (account_id, folder, int(info[-1]))
    _store_folder_counts(client, db, account_id, folder, exists)
    status, data = client.uid("SEARCH", None, *criteria)
    if status != "OK" or not data or data[0] is None:
        raise ValueError("Der Server konnte die Nachrichtenliste nicht liefern.")
    known = {r[0] for r in db.execute(
        "SELECT uid FROM mails WHERE account_id=? AND folder=? AND validity=?", key
    )}
    pending = sorted({int(uid) for uid in data[0].split()} - known, reverse=True)
    saved, missing = 0, 0
    progress(0, len(pending))
    for index, uid in enumerate(pending, 1):
        if cancelled.is_set():
            break
        try:
            status, response = client.uid("FETCH", str(uid), "(UID INTERNALDATE BODY.PEEK[])")
        except imaplib.IMAP4.abort:
            raise  # The connection is gone; the next run continues with the remaining mails.
        except imaplib.IMAP4.error:
            status, response = "NO", []
        if status != "OK":
            # Skip this mail so that one faulty message cannot block all older ones.
            missing += 1
            if index % 10 == 0 or index == len(pending):
                progress(index, len(pending))
            continue
        for position, item in enumerate(response):
            if not isinstance(item, tuple):
                continue
            metadata, raw = item
            # imaplib returns the attributes after a message literal separately.
            if position + 1 < len(response) and isinstance(response[position + 1], bytes):
                metadata += b" " + response[position + 1]
            if not re.search(rb"\bUID " + str(uid).encode() + rb"\b", metadata, re.I):
                continue
            internal = re.search(rb'INTERNALDATE "([^"]+)"', metadata, re.I)
            try:
                received = int(parsedate_to_datetime(
                    internal[1].decode().replace("-", " ", 2).strip()
                ).timestamp()) if internal else 0
            except (ValueError, TypeError, OverflowError, OSError):
                received = 0
            with db:
                cursor = db.execute(
                    """INSERT INTO mails
                       (mail_id,account_id,folder,validity,uid,sender,recipient,subject,sent,body,raw,
                        has_attachments,spam_score,spam_flag,dmarc_fail,spam_checked)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(account_id,folder,validity,uid) DO NOTHING""",
                    (str(uuid4()), *key, uid, *safe_parse_mail(raw, received), raw, has_attachments(raw), *_spam_columns(raw))
                )
                if cursor.rowcount:
                    _index_new_mail(db, cursor.lastrowid)
            saved += cursor.rowcount
            break
        else:
            missing += 1
        if index % 10 == 0 or index == len(pending):
            progress(index, len(pending))
    return saved, missing
