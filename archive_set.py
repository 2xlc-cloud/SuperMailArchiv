"""The archive folder: one database file per account (since 1.18.0), used like a single archive.

Each account file is a complete archive in the format of archive.py (accounts, folders, options, mails, search
index). A file usually holds one account; editing an account's server or user keeps the old entry (with its mails)
in the same file, as before.

Keys used by the program:
  account  "Konto-3#1"   (file Konto-3.sqlite3, account id 1 in that file)
  mail     "Konto-3:57"  (file Konto-3.sqlite3, mail id 57 in that file)
"""
import heapq
import re
import sqlite3
from contextlib import closing
from itertools import islice
from pathlib import Path

import archive as store

FILE_PATTERN = "Konto-*.sqlite3"
# Also matches "Konto-2.sqlite3.teil" (files being written), so numbers are never given twice.
NUMBER = re.compile(r"Konto-(\d+)\.sqlite3", re.I)


def file_number(path):
    match = NUMBER.search(Path(path).name)
    return int(match[1]) if match else 0


def account_files(folder):
    return sorted((path for path in Path(folder).glob(FILE_PATTERN) if NUMBER.fullmatch(path.name)),
                  key=file_number)


def next_file(folder):
    numbers = [file_number(path) for path in Path(folder).glob("Konto-*.sqlite3*")]
    return Path(folder) / f"Konto-{max(numbers, default=0) + 1}.sqlite3"


class ArchiveSet:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        for path in self.files():
            store.initialize(path)
            if _empty(path):  # Left over when deleting an account while the file was in use.
                try:
                    _remove_file(path)
                except OSError:
                    pass

    # --- Files and keys --------------------------------------------------------------------------------------

    def files(self):
        return account_files(self.folder)

    def file(self, stem):
        return self.folder / f"{stem}.sqlite3"

    @staticmethod
    def account_key(path, account_id):
        return f"{Path(path).stem}#{account_id}"

    def account_place(self, key):
        stem, _, account_id = key.rpartition("#")
        return self.file(stem), int(account_id)

    @staticmethod
    def mail_key(path, mail_id):
        return f"{Path(path).stem}:{mail_id}"

    def mail_place(self, key):
        stem, _, mail_id = key.rpartition(":")
        return self.file(stem), int(mail_id)

    def group_mails(self, keys):
        """{file: [mail ids]} for mail keys."""
        groups = {}
        for key in keys:
            path, mail_id = self.mail_place(key)
            groups.setdefault(path, []).append(mail_id)
        return groups

    # --- Accounts --------------------------------------------------------------------------------------------

    def _records(self):
        """(file, account id, account, enabled) of all files."""
        for path in self.files():
            with closing(store.connect(path)) as db:
                for account_id, account, _, enabled in store._accounts(db):
                    yield path, account_id, account, enabled

    def saved_accounts(self):
        accounts = [(self.account_key(path, account_id), account)
                    for path, account_id, account, enabled in self._records() if enabled]
        return dict(sorted(accounts, key=lambda item: item[1].label.casefold()))

    def save_account(self, account, key=None):
        """Store a new or changed account; a new account gets its own file. Returns the account key."""
        match = next(((path, account_id, enabled) for path, account_id, found, enabled in self._records()
                      if found == account), None)
        if key is not None and match is not None and self.account_key(*match[:2]) == key:
            return key
        if match is not None and match[2]:
            raise sqlite3.IntegrityError("Konto bereits vorhanden.")
        if match is not None:
            # The same account was removed earlier: enable it again in its file (with its mails).
            if key is not None:
                self.remove_account(key)
            return self.account_key(match[0], store.save_account(match[0], account))
        if key is not None:
            path, account_id = self.account_place(key)
            return self.account_key(path, store.save_account(path, account, account_id))
        path = next_file(self.folder)
        store.initialize(path)
        try:
            return self.account_key(path, store.save_account(path, account))
        except BaseException:
            _remove_file(path)
            raise

    def remove_account(self, key):
        store.remove_account(*self.account_place(key))

    def account_password(self, key):
        return store.account_password(*self.account_place(key))

    def remember_password(self, key, account, password):
        path, account_id = self.account_place(key)
        store.remember_password(path, account_id, account, password)

    def get_period(self, key):
        return store.get_period(*self.account_place(key))

    def set_period(self, key, date_from, date_to):
        store.set_period(*self.account_place(key), date_from, date_to)

    def get_skip_deleted(self, key):
        return store.get_skip_deleted(*self.account_place(key))

    def set_skip_deleted(self, key, skip):
        store.set_skip_deleted(*self.account_place(key), skip)

    def account_folders(self, key):
        return store.account_folders(*self.account_place(key))

    def set_folder_selection(self, key, names, selected):
        store.set_folder_selection(*self.account_place(key), names, selected)

    def folder_tree(self):
        tree = [(self.account_key(path, account_id), account, folders)
                for path in self.files() for account_id, account, folders in store.folder_tree(path)]
        return sorted(tree, key=lambda item: item[1].label.casefold())

    def load_folders(self, key, account, password):
        return store.load_folders(self.account_place(key)[0], account, password)

    def sync(self, key, account, password, progress, cancelled):
        return store.sync(self.account_place(key)[0], account, password, progress, cancelled)

    # --- Mails -----------------------------------------------------------------------------------------------

    def count_mails(self, key=None, folders=None, mail_keys=None):
        if mail_keys is not None:
            return sum(store.count_mails(path, mail_ids=ids) for path, ids in self.group_mails(mail_keys).items())
        if key is not None:
            return store.count_mails(*self.account_place(key), folders)
        return sum(store.count_mails(path) for path in self.files())

    def delete_mails(self, mail_keys):
        return sum(store.delete_mails(path, ids) for path, ids in self.group_mails(mail_keys).items())

    def delete_folder_mails(self, key, folders):
        return store.delete_folder_mails(*self.account_place(key), folders)

    def delete_account(self, key):
        """Delete an account with all its mails; a file without accounts left is removed."""
        path, account_id = self.account_place(key)
        deleted = store.delete_account(path, account_id)  # Overwrites the deleted content (secure_delete).
        if _empty(path):
            try:
                _remove_file(path)
            except OSError:
                pass  # Still open in the background; removed at the next start.
        return deleted

    def find_mails(self, query, offset=0, limit=100, account_key=None, folder=None, sort="date", descending=True,
                   sender=None, only_suspicious=False, threshold=5):
        """Like archive.find_mails over all files (or one account); each row gets its mail key as "key"."""
        options = dict(sort=sort, descending=descending, sender=sender, only_suspicious=only_suspicious,
                       threshold=threshold)
        if account_key is not None:
            path, account_id = self.account_place(account_key)
            return [dict(row, key=self.mail_key(path, row["id"]))
                    for row in store.find_mails(path, query, offset, limit, account_id, folder, **options)]
        files = self.files()
        if len(files) == 1:
            return [dict(row, key=self.mail_key(files[0], row["id"]))
                    for row in store.find_mails(files[0], query, offset, limit, **options)]
        # Every file delivers its first offset+limit mails in the same order; merging them gives the page. Only
        # the rows of the page itself are converted (deep pages merge tens of thousands of rows).
        lists = [[(row[10], row[3], number, row[0], row)
                  for row in store.find_mails(path, query, 0, offset + limit, **options)]
                 for number, path in enumerate(files)]
        merged = heapq.merge(*lists, key=lambda item: item[:4], reverse=descending)
        return [dict(item[4], key=self.mail_key(files[item[2]], item[3]))
                for item in islice(merged, offset, offset + limit)]

    def get_mail(self, mail_key, raw=False):
        return store.get_mail(*self.mail_place(mail_key), raw=raw)

    def resend_groups(self, mail_keys):
        """{account key: (account, [mail keys])}: the mails to put back, by the account they were archived from."""
        groups = {}
        for mail_key in mail_keys:
            path, _ = self.mail_place(mail_key)
            row = self.get_mail(mail_key)
            if row is None:
                raise LookupError("Die Mail ist nicht mehr im Archiv.")
            account = store.Account(row["host"], row["port"], row["user"])
            groups.setdefault(self.account_key(path, row["account_id"]), (account, []))[1].append(mail_key)
        return groups

    def resend(self, account_key, account, password, mail_keys):
        path, _ = self.account_place(account_key)
        return store.resend(path, account, password, [self.mail_place(key)[1] for key in mail_keys])

    # --- Background work over all files ----------------------------------------------------------------------

    def list_indexes_ready(self):
        return all(store.list_indexes_ready(path) for path in self.files())

    def create_list_indexes(self):
        for path in self.files():
            if not store.list_indexes_ready(path):
                store.create_list_indexes(path)

    def build_search_index(self, batch=2000):
        """Index the next batch in the first file that needs it. Returns (indexed, total) over all files."""
        indexed = total = 0
        worked = False
        for path in self.files():
            if not worked:
                done, count = store.build_search_index(path, batch)
                worked = done < count or worked
            else:
                with closing(store.connect(path)) as db:
                    upto = db.execute("SELECT value FROM search_state WHERE name='upto'").fetchone()[0]
                    done, count = db.execute("SELECT count(*) FILTER (WHERE id <= ?), count(*) FROM mails",
                                             (upto,)).fetchone()
            indexed, total = indexed + done, total + count
        return indexed, total

    def unknown_spam(self, limit=500, mail_ids=None):
        return self._unknown(store.unknown_spam, limit, mail_ids)

    def unknown_attachment_flags(self, limit=200, mail_ids=None):
        return self._unknown(store.unknown_attachment_flags, limit, mail_ids)

    def _unknown(self, function, limit, mail_keys):
        if mail_keys is not None:
            return [(self.mail_key(path, mail_id), data) for path, ids in self.group_mails(mail_keys).items()
                    for mail_id, data in function(path, mail_ids=ids)]
        for path in self.files():
            batch = function(path, limit)
            if batch:
                return [(self.mail_key(path, mail_id), data) for mail_id, data in batch]
        return []

    def store_spam(self, rated):
        for path, items in self._by_file(rated).items():
            store.store_spam(path, items)

    def store_attachment_flags(self, flags):
        for path, items in self._by_file(flags).items():
            store.store_attachment_flags(path, items)

    def _by_file(self, values):
        groups = {}
        for key, value in values.items():
            path, mail_id = self.mail_place(key)
            groups.setdefault(path, {})[mail_id] = value
        return groups

    def spam_progress(self):
        return _total(store.spam_progress(path) for path in self.files())

    def attachment_progress(self):
        return _total(store.attachment_progress(path) for path in self.files())


def _total(pairs):
    done = total = 0
    for pair in pairs:
        done, total = done + pair[0], total + pair[1]
    return done, total


def _empty(path):
    """True for an account file without any account and mail."""
    with closing(store.connect(path)) as db:
        return not db.execute("SELECT EXISTS(SELECT 1 FROM accounts) OR EXISTS(SELECT 1 FROM mails)").fetchone()[0]


def _remove_file(path):
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
