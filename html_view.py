"""HTML view of a mail (tab "HTML" in the detail window), rendered with tkinterweb (Tkhtml).

Safety: scripts never run (JavaScript is off and <script> is removed), forms and embedded objects are off, and no
file or web address is loaded except images contained in the mail itself (cid:, data:). Images from web servers
(often tracking pixels that tell the sender when a mail was opened) are blocked until the user loads them for this
one mail. Links open in the default browser only after a click.
"""
import re
import threading
import time
import tkinter as tk
import webbrowser
from email import policy
from email.parser import BytesParser
from tkinter import ttk
from urllib.parse import unquote
from urllib.request import Request, urlopen

MAX_REMOTE_IMAGE = 5 * 1024 * 1024
REMOTE_TIMEOUT = 10

# Removed before rendering: active content and anything that would navigate or change how addresses resolve.
DROP_ELEMENTS = re.compile(r"<(script|iframe|frame|frameset|object|embed|applet|noscript)\b.*?(</\1\s*>|$)",
                           re.I | re.S)
DROP_TAGS = re.compile(r"<(?:meta\b[^>]*http-equiv\s*=\s*[\"']?\s*refresh[^>]*|base\b[^>]*|link\b[^>]*)>", re.I)
REMOTE_URL = re.compile(r"""(?:\bsrc\s*=\s*["']?|\bbackground\s*=\s*["']?|url\(\s*["']?)\s*(https?:)?//""", re.I)


def html_content(raw):
    """(HTML text, {content id: (bytes, content type)}) of a mail; (None, {}) without an HTML part."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        part = message.get_body(preferencelist=("html",))
        if part is None:
            return None, {}
        try:
            html = part.get_content()
        except (LookupError, UnicodeError, KeyError):
            html = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        images = {}
        for item in message.walk():
            content_id = item.get("Content-ID")
            if content_id and item.get_content_maintype() == "image":
                data = item.get_payload(decode=True)
                if data:
                    images[content_id.strip().strip("<>").casefold()] = (data, item.get_content_type())
        return html, images
    except Exception:  # A malformed mail is shown as text only.
        return None, {}


def clean_html(html):
    """HTML without active content; returns (HTML, number of references to images on web servers)."""
    html = DROP_ELEMENTS.sub("", html)
    html = DROP_TAGS.sub("", html)
    return html, len(REMOTE_URL.findall(html))


class HtmlView(ttk.Frame):
    """Rendered HTML of one mail with a bar to load blocked images from the web."""

    def __init__(self, parent, raw, status):
        super().__init__(parent)
        self.raw = raw
        self.status = status
        self.allow_remote = False
        self.blocked = set()
        self.lock = threading.Lock()
        self.last_click = 0.0
        self.frame = None
        html, self.images = html_content(raw)
        self.html, self.remote = clean_html(html or "")
        self.bar = ttk.Frame(self, padding=(8, 6))
        self.notice = ttk.Label(self.bar, style="Muted.TLabel")
        self.notice.pack(side="left")
        self.load_button = ttk.Button(self.bar, text="Externe Bilder laden", command=self.load_remote)
        self.load_button.pack(side="right")
        if self.remote:
            self.notice.configure(text="Bilder aus dem Internet wurden blockiert (Schutz vor Lesebestätigung "
                                       "über Tracking-Pixel).")
            self.bar.pack(fill="x")
        self.rendered = False

    @property
    def available(self):
        return bool(self.html.strip())

    def render(self):
        """Build the HTML widget (on first display of the tab; loading Tkhtml takes a moment)."""
        if self.rendered or not self.available:
            return
        self.rendered = True
        from tkinterweb import HtmlFrame  # Imported late: only needed when an HTML mail is shown.

        self.frame = HtmlFrame(self, messages_enabled=False, javascript_enabled=False, forms_enabled=False,
                               objects_enabled=False, caches_enabled=False, request_func=self.request,
                               on_link_click=self.open_link, selection_enabled=True)
        self.frame.pack(fill="both", expand=True)
        self.frame.html.bind("<Button-1>", self.remember_click, add="+")
        self.frame.load_html(self.html, base_url="about:blank")

    def remember_click(self, _event):
        self.last_click = time.monotonic()

    def load_remote(self):
        self.allow_remote = True
        self.bar.pack_forget()
        if self.frame is not None:
            self.frame.load_html(self.html, base_url="about:blank")

    def request(self, url, data="", method="GET", decode=None, *rest):
        """All resources of the page: images of the mail itself, web images only when allowed, nothing else."""
        lower = url.lower()
        if lower.startswith("cid:"):
            found = self.images.get(unquote(url[4:]).strip().strip("<>").casefold())
            if found is None:
                raise OSError(f"Bild {url} ist nicht in der Mail enthalten")
            return url, found[0], found[1], 200
        if lower.startswith("data:"):
            with urlopen(url) as response:  # Decodes the data: address itself; no network access.
                return url, response.read(), response.info().get_content_type(), 200
        if lower.startswith(("http://", "https://")) and self.allow_remote and method == "GET":
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=REMOTE_TIMEOUT) as response:
                content = response.read(MAX_REMOTE_IMAGE + 1)
                if len(content) > MAX_REMOTE_IMAGE:
                    raise OSError("Bild zu groß")
                return response.geturl(), content, response.info().get_content_type(), response.getcode()
        with self.lock:
            self.blocked.add(url)
        raise OSError(f"Blockiert: {url}")

    def open_link(self, url):
        """Open a clicked link in the browser; never without a click (e.g. a redirect inside the mail)."""
        if time.monotonic() - self.last_click > 1.5:
            return
        if not re.match(r"(?:https?://|mailto:)", url, re.I):
            self.status.set(f"Link nicht geöffnet (nur http, https und mailto): {url}")
            return
        try:
            webbrowser.open(url)
            self.status.set(f"Link geöffnet: {url}")
        except Exception as error:
            self.status.set(f"Link konnte nicht geöffnet werden: {error}")
