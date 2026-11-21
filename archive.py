import imaplib
import json
import re
import sqlite3
import ssl
from contextlib import closing
from dataclasses import astuple, dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from uuid import uuid4

from protection import seal, unseal


@dataclass(frozen=True)
class Account:
    host: str
    port: int
    user: str
    folder: str

    @property
    def label(self):
        return f"{self.user} · {self.host}:{self.port} / {self.folder}"


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


class TextFromHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and tag in {"script", "style", "head"}:
            self.hidden -= 1
        elif not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

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
            parser = TextFromHTML()
            parser.feed(body)
            parser.close()
            body = parser.text()
        if body:
            break
    return header("From"), header("To"), header("Subject"), timestamp, body


def connect(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    db.create_function("fold", 1, str.casefold, deterministic=True)
    return db


def _create_schema(db):
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


def _protect_account(account, password=None):
    return seal(json.dumps([astuple(account), password], ensure_ascii=False).encode("utf-8"))


def _accounts(db):
    for row in db.execute("SELECT id,protected,enabled FROM accounts"):
        try:
            fields, password = json.loads(unseal(row["protected"]))
            account = Account(*fields)
        except (OSError, ValueError, TypeError) as error:
            raise ValueError(
                "Kontodaten lassen sich nicht entschlüsseln. Öffne das Archiv mit dem ursprünglichen Windows-Konto."
            ) from error
        yield row["id"], account, password, bool(row["enabled"])


def _insert_account(db, account, enabled=True, password=None):
    return db.execute("INSERT INTO accounts(protected,enabled) VALUES (?,?)",
                      (_protect_account(account, password), enabled)).lastrowid


def _migrate(db, legacy_path, version):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "mails" in tables:
        db.execute("ALTER TABLE mails RENAME TO old_mails")
    if "accounts" in tables:
        db.execute("ALTER TABLE accounts RENAME TO old_accounts")
    _create_schema(db)
    fields = "host,port,username,folder"
    enabled = set()
    if "accounts" in tables:
        enabled.update(Account(*row) for row in db.execute(f"SELECT {fields} FROM old_accounts"))
    if version == 0:
        try:
            old = json.loads(legacy_path.read_text(encoding="utf-8"))
            enabled.add(Account(old["host"], int(old["port"]), old["user"], old["folder"]))
        except FileNotFoundError:
            pass
    archived = set()
    if "mails" in tables:
        archived.update(Account(*row) for row in db.execute(f"SELECT DISTINCT {fields} FROM old_mails"))
    db.create_function("new_mail_id", 0, lambda: str(uuid4()))
    for account in enabled | archived:
        key = _insert_account(db, account, enabled=account in enabled)
        if account in archived:
            db.execute("""
                INSERT INTO mails(id,mail_id,account_id,validity,uid,sender,recipient,subject,sent,body,raw)
                SELECT id,new_mail_id(),?,validity,uid,sender,recipient,subject,sent,body,raw
                FROM old_mails WHERE host=? AND port=? AND username=? AND folder=?
            """, (key, *astuple(account)))
    if "mails" in tables:
        db.execute("DROP TABLE old_mails")
    if "accounts" in tables:
        db.execute("DROP TABLE old_accounts")
    db.execute("PRAGMA user_version=2")


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path = path.with_name("konto.json")
    with closing(connect(path)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > 3:
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


def saved_accounts(path):
    with closing(connect(path)) as db:
        accounts = [(key, account) for key, account, _, enabled in _accounts(db) if enabled]
    return dict(sorted(accounts, key=lambda item: item[1].label.casefold()))


def archived_accounts(path):
    with closing(connect(path)) as db:
        archived = {row[0] for row in db.execute("SELECT DISTINCT account_id FROM mails")}
        accounts = [account for key, account, _, enabled in _accounts(db) if enabled or key in archived]
    return sorted(accounts, key=lambda account: account.label.casefold())


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


def find_mails(path, query, offset=0, limit=100, account=None):
    terms = query.casefold().split()
    where = " AND ".join(
        "instr(fold(mail_id || ' ' || subject || ' ' || sender || ' ' || recipient || ' ' || body), ?) > 0"
        for _ in terms
    ) or "1"
    params = tuple(terms)
    with closing(connect(path)) as db:
        if account is not None:
            where += " AND account_id=?"
            params += (_account_key(db, account),)
        return db.execute(
            f"SELECT id, sender, subject, sent FROM mails WHERE {where} "
            "ORDER BY sent DESC, id DESC LIMIT ? OFFSET ?", (*params, limit, offset)
        ).fetchall()


def get_mail(path, mail_id, raw=False):
    with closing(connect(path)) as db:
        if raw:
            return db.execute("SELECT raw FROM mails WHERE id=?", (mail_id,)).fetchone()
        row = db.execute("""
            SELECT sender, recipient, subject, sent, body, mail_id, account_id
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


def clean_password(password):
    # Copying a password can include the line ending of the source field.
    password = password.strip("\r\n")
    if not password:
        raise ValueError("Bitte gib ein Passwort oder App-Passwort ein.")
    if any(c in password for c in "\r\n\0"):
        raise ValueError("Das Passwort enthält einen Zeilenumbruch oder ein Nullzeichen. Kopiere es als eine Zeile.")
    return password


def sync(path, account, password, progress, cancelled):
    password = clean_password(password)
    if any(c in account.user for c in "\r\n\0"):
        raise ValueError("Der Benutzername enthält einen Zeilenumbruch oder ein Nullzeichen.")
    with imaplib.IMAP4_SSL(account.host, account.port, ssl_context=ssl.create_default_context(),
                          timeout=25) as client, closing(connect(path)) as db:
        try:
            user = '"' + account.user.replace("\\", "\\\\").replace('"', '\\"') + '"'
            client.login(user, password)
        except imaplib.IMAP4.error:
            raise ValueError("Anmeldung fehlgeschlagen. Prüfe Benutzername und (App-)Passwort.") from None
        status, _ = client.select(mailbox_argument(account.folder), readonly=True)
        if status != "OK":
            raise ValueError("Der IMAP-Ordner konnte nicht geöffnet werden.")
        _, info = client.response("UIDVALIDITY")
        if not info or not info[-1] or not info[-1].isdigit() or int(info[-1]) == 0:
            raise ValueError("Der Server hat keine gültige UIDVALIDITY geliefert.")
        with db:
            db.execute("BEGIN IMMEDIATE")
            account_id = _account_key(db, account)
            if account_id is None:
                account_id = _insert_account(db, account, enabled=False)
        key = (account_id, int(info[-1]))
        status, data = client.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or data[0] is None:
            raise ValueError("Der Server konnte die Nachrichtenliste nicht liefern.")
        known = {r[0] for r in db.execute(
            "SELECT uid FROM mails WHERE account_id=? AND validity=?", key
        )}
        pending = sorted({int(uid) for uid in data[0].split()} - known, reverse=True)
        saved, missing = 0, 0
        progress(0, len(pending))
        for index, uid in enumerate(pending, 1):
            if cancelled.is_set():
                break
            status, response = client.uid("FETCH", str(uid), "(UID INTERNALDATE BODY.PEEK[])")
            if status != "OK":
                raise ValueError(f"Nachricht {uid} konnte nicht abgerufen werden. Gesicherte Mails bleiben erhalten.")
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
                           (mail_id,account_id,validity,uid,sender,recipient,subject,sent,body,raw)
                           VALUES (?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(account_id,validity,uid) DO NOTHING""",
                        (str(uuid4()), *key, uid, *parse_mail(raw, received), raw)
                    )
                saved += cursor.rowcount
                break
            else:
                missing += 1
            if index % 10 == 0 or index == len(pending):
                progress(index, len(pending))
        return saved, missing
