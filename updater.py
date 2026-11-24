"""Update check and installation through GitHub releases (adopted from SuperPyBackup).

Safety rules
  * Checking never downloads a program.
  * Installing only after explicit confirmation - except the mandatory update: if more than FORCE_AFTER_VERSIONS newer
    versions exist, the automatic check installs without asking. All other checks below apply unchanged.
  * Download addresses must belong to the configured repository; size and SHA-256 (SHA256SUMS.txt of the release) are
    checked.
  * Signature: every release contains SHA256SUMS.txt.sig, an Ed25519 signature of SHA256SUMS.txt. The public key is
    built into the program (RELEASE_PUBLIC_KEYS); the secret key stays with the publisher (tools/sign_release.py), never
    on GitHub. Without a valid signature nothing is installed, so a manipulated repository or a taken-over GitHub account
    is not enough to bring in malware.
  * A downloaded program is only run after all checks passed, and then only with --version (smoke test).
  * Replacement with rollback: on any error the current installation stays unchanged.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import ed25519
from app_info import APP_NAME, LEGACY_NAME, VERSION_TEXT

REPOSITORY = "2xlc-cloud/SuperMailArchiv"
API_URL = "https://api.github.com"
# Installs from before the renaming (1.23.0) still hold SuperMailBackup.exe; it is updated under that name.
ASSETS = (f"{APP_NAME}.exe", f"{LEGACY_NAME}.exe")
SUMS_NAME = "SHA256SUMS.txt"
SIG_NAME = "SHA256SUMS.txt.sig"
# Public keys (hex) of the release signature; several allow a key change (old and new at the same time).
RELEASE_PUBLIC_KEYS = ("43704c4e3311b67e752e73cb5b6f79d9c346194a1161fee16276bd07d1b149fb",)
FORCE_AFTER_VERSIONS = 5
CHECK_INTERVAL_HOURS = 12
MAX_JSON = 8 * 1024 * 1024
MAX_BINARY = 200 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    tag: str
    assets: dict
    page: str = ""
    behind: int = 1  # Number of published versions newer than the installed one.
    forced: bool = False  # Mandatory update: more than FORCE_AFTER_VERSIONS behind.


def parse_version(text):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text).strip())
    if not match:
        raise UpdateError(f"Keine gültige Versionsnummer (X.Y.Z): {text!r}")
    return tuple(int(part) for part in match.groups())


def _check_url(url):
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
        return  # Local tests only.
    raise UpdateError(f"Nur HTTPS-Adressen sind erlaubt: {url}")


def default_fetch(url, limit):
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}-Updater",
                                                   "Accept": "application/vnd.github+json, application/octet-stream"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - scheme checked above
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NoRelease() from error
        raise UpdateError(f"Fehler der Update-Quelle ({error.code}) bei {url}") from error
    except OSError as error:
        raise UpdateError(f"Netzwerkfehler bei {url}: {error}") from error
    if len(data) > limit:
        raise UpdateError(f"Antwort zu groß: {url}")
    return data


class NoRelease(UpdateError):
    """The repository has no published release yet."""


def _web_base(api_url):
    api = api_url.rstrip("/")
    return "https://github.com" if api == "https://api.github.com" else api


def latest_release(fetch=default_fetch, api_url=API_URL, repository=REPOSITORY):
    raw = fetch(f"{api_url.rstrip('/')}/repos/{repository}/releases/latest", MAX_JSON)
    try:
        data = json.loads(raw.decode("utf-8"))
        tag = data["tag_name"]
        parse_version(tag)
        if data.get("draft") or data.get("prerelease"):
            raise UpdateError("Neueste Release ist ein Entwurf oder eine Vorabversion.")
        assets = {}
        for asset in data["assets"]:
            name, url, size = asset["name"], asset["browser_download_url"], asset["size"]
            if not isinstance(name, str) or not isinstance(url, str) or not isinstance(size, int):
                raise UpdateError("Ungültige Asset-Angaben.")
            assets[name] = {"url": url, "size": size}
    except (ValueError, KeyError, TypeError) as error:
        raise UpdateError(f"Unerwartete Antwort der Update-Quelle: {error}") from error
    prefix = f"{_web_base(api_url)}/{repository}/releases/download/"
    for asset in assets.values():
        if not str(asset["url"]).startswith(prefix):
            raise UpdateError(f"Download-Adresse gehört nicht zum Repository {repository}: {asset['url']}")
    return UpdateInfo(version=tag.lstrip("v"), tag=tag, assets=assets, page=str(data.get("html_url", "")))


def count_newer(current, fetch=default_fetch, api_url=API_URL, repository=REPOSITORY):
    """Number of published versions (no drafts/pre-releases) newer than current; at least 1."""
    try:
        data = json.loads(fetch(f"{api_url.rstrip('/')}/repos/{repository}/releases?per_page=100",
                                MAX_JSON).decode("utf-8"))
        have = parse_version(current)
        newer = {parse_version(release["tag_name"]) for release in data
                 if not release.get("draft") and not release.get("prerelease")}
        return max(1, len({version for version in newer if version > have}))
    except (UpdateError, ValueError, KeyError, TypeError, AttributeError):
        return 1  # List not readable: the newest version is newer in any case.


def check_for_update(current=VERSION_TEXT, fetch=default_fetch, api_url=API_URL, repository=REPOSITORY):
    """The newer release or None. Downloads nothing. Sets behind and forced (mandatory update)."""
    try:
        info = latest_release(fetch, api_url, repository)
    except NoRelease:
        return None
    if parse_version(info.version) <= parse_version(current):
        return None
    info.behind = count_newer(current, fetch, api_url, repository)
    info.forced = FORCE_AFTER_VERSIONS > 0 and info.behind > FORCE_AFTER_VERSIONS
    return info


def already_notified(folder, version):
    """Was this version already announced? Then the 12-hour check does not repeat the notice."""
    try:
        return json.loads((folder / "update-gemeldet.json").read_text(encoding="utf-8")).get("version") == version
    except (OSError, ValueError, AttributeError):
        return False


def remember_notified(folder, version):
    try:
        (folder / "update-gemeldet.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    except OSError:
        pass


def parse_sums(text):
    sums = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Fa-f0-9]{64}) [ *]?(.+)", line.strip())
        if match:
            sums[match.group(2).strip()] = match.group(1).lower()
    return sums


def verify_signature(sums, signature_text, keys=None):
    """Checks the Ed25519 signature (Base64) of the exact content of SHA256SUMS.txt; UpdateError if no key fits."""
    try:
        signature = base64.b64decode(signature_text.strip(), validate=True)
    except (binascii.Error, ValueError) as error:
        raise UpdateError("Die Signatur der Release ist nicht lesbar.") from error
    for key in (RELEASE_PUBLIC_KEYS if keys is None else keys):
        try:
            if ed25519.verify(bytes.fromhex(key), sums, signature):
                return
        except ValueError:
            continue
    raise UpdateError("Die Signatur der Release ist ungültig: die Prüfsummen stammen nicht vom Herausgeber. "
                      "Es wird nichts installiert.")


def _default_smoke_test(path, version):
    result = subprocess.run([path, "--version"], capture_output=True, timeout=60, creationflags=0x08000000)
    output = (result.stdout or b"").decode("utf-8", "replace")
    if result.returncode != 0 or version not in output:
        raise UpdateError(f"Das heruntergeladene Programm meldet nicht die erwartete Version {version}: "
                          f"{output.strip()!r}")


def installed_dir():
    """Folder of the installed EXE; None when started from source (nothing to replace there)."""
    return str(Path(sys.executable).parent) if getattr(sys, "frozen", False) else None


def install_update(info, install_dir, *, fetch=default_fetch, smoke_test=_default_smoke_test, keys=None):
    """Downloads, checks and replaces the program. Returns the replaced files. Confirmation is the caller's job."""
    names = [name for name in ASSETS if os.path.isfile(os.path.join(install_dir, name))]
    if not names:
        raise UpdateError(f"Im Programmordner wurde kein Programm gefunden: {install_dir}")
    missing = [name for name in names + [SUMS_NAME, SIG_NAME] if name not in info.assets]
    if missing:
        raise UpdateError(f"Die Release enthält nicht alle benötigten Dateien: {', '.join(missing)}")
    work = os.path.join(install_dir, f".update-{os.urandom(6).hex()}")
    os.mkdir(work)
    replaced = []
    try:
        sums_bytes = fetch(str(info.assets[SUMS_NAME]["url"]), 64 * 1024)
        verify_signature(sums_bytes, fetch(str(info.assets[SIG_NAME]["url"]), 4096).decode("ascii", "replace"),
                         keys)
        sums = parse_sums(sums_bytes.decode("utf-8", "replace"))
        for name in names:
            if name not in sums:
                raise UpdateError(f"Für {name} fehlt die SHA-256-Summe in {SUMS_NAME}.")
            data = fetch(str(info.assets[name]["url"]), MAX_BINARY)
            if len(data) != info.assets[name]["size"]:
                raise UpdateError(f"{name}: Größe stimmt nicht (erwartet {info.assets[name]['size']}, erhalten "
                                  f"{len(data)}); Download unvollständig?")
            if hashlib.sha256(data).hexdigest() != sums[name]:
                raise UpdateError(f"{name}: SHA-256 stimmt nicht; Datei ist beschädigt oder manipuliert.")
            with open(os.path.join(work, name), "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        for name in names:
            smoke_test(os.path.join(work, name), info.version)
        for name in names:
            # A running EXE can be renamed on Windows, so the program can replace itself.
            current = os.path.join(install_dir, name)
            backup = current + ".old"
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(current, backup)
            replaced.append(name)
            os.replace(os.path.join(work, name), current)
        return names
    except BaseException:
        for name in replaced:  # Rollback
            current = os.path.join(install_dir, name)
            backup = current + ".old"
            try:
                if os.path.exists(backup):
                    if os.path.exists(current):
                        os.remove(current)
                    os.replace(backup, current)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rollback(install_dir):
    """Restores the programs saved before the last update (.old)."""
    restored = []
    for name in ASSETS:
        current = os.path.join(install_dir, name)
        backup = current + ".old"
        if os.path.isfile(backup):
            if os.path.exists(current):
                os.replace(current, current + ".bad")
            os.replace(backup, current)
            restored.append(name)
            try:
                os.remove(current + ".bad")
            except OSError:
                pass
    if not restored:
        raise UpdateError("Es gibt keine gesicherte Vorversion (.old).")
    return restored
