#!/usr/bin/env python3
"""
Sadaqah-e-Jariyah — local browser.

    python3 serve.py              # rebuilds the index, opens http://localhost:8000
    python3 serve.py 9000         # different port
    python3 serve.py --lan        # also reachable by others on the same Wi-Fi
    python3 serve.py --no-open    # don't open a browser tab

No dependencies beyond Python 3.8+. Supports HTTP range requests so videos seek and big PDFs stream.
"""
import argparse
import mimetypes
import os
import socket
import sys
import threading
import urllib.parse
import webbrowser
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
sys.path.insert(0, SITE)
import build_index  # noqa: E402

BLOCKED_TOP = {".git", "_Inbox", "site", "catalog", "tools", "worker", "setup", ".sej-cache", "_dist", "_Sources"}
TEXTY = (".c", ".cpp", ".h", ".hpp", ".java", ".py", ".md", ".txt", ".csv", ".tsv", ".ipynb", ".json", ".m", ".sql", ".sh")
for ext, t in {".ipynb": "application/json", ".md": "text/markdown", ".mp4": "video/mp4", ".m4v": "video/mp4",
               ".webm": "video/webm", ".mov": "video/quicktime", ".tsv": "text/tab-separated-values",
               ".js": "text/javascript", ".mjs": "text/javascript"}.items():
    mimetypes.add_type(t, ext)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SadaqahEJariyah/1.0"

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith(("4", "5")) and str(args[1]) != "416":
            sys.stderr.write("  %s %s\n" % (args[1], urllib.parse.unquote(self.path)))

    def _resolve(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path.startswith("/files/"):
            base, rel = ROOT, path[len("/files/"):]
            parts = [p for p in rel.split("/") if p]
            if not parts or parts[0] in BLOCKED_TOP:
                return None
        else:
            base, rel = SITE, path.lstrip("/")
            if rel == "" or rel.endswith("/"):
                rel += "index.html"
            parts = [p for p in rel.split("/") if p]
        if any(p == ".." or p.startswith(".") for p in parts):
            return None
        full = os.path.realpath(os.path.join(base, *parts))
        if not full.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(full):
            if base == SITE and "." not in parts[-1]:  # a page address (/Faculty-of-Science/BCA/): the app routes it
                return os.path.join(SITE, "index.html")
            return None
        return full

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve()

    def _serve(self, head=False):
        full = self._resolve()
        if not full:
            self.send_error(404, "Not found")
            return
        st = os.stat(full)
        size = st.st_size
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or full.endswith(TEXTY) or ctype in ("application/json", "text/javascript"):
            ctype = ctype.split(";")[0] + "; charset=utf-8"

        start, end, status = 0, max(size - 1, 0), 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes=") and size:
            try:
                s, e = rng[6:].split(",")[0].strip().split("-")
                if s == "":
                    start = max(0, size - int(e))
                else:
                    start = int(s)
                    end = min(int(e), size - 1) if e else size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            except ValueError:
                pass
        length = (end - start + 1) if size else 0

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
        self.send_header("Cache-Control", "no-cache" if full.startswith(SITE) else "max-age=3600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head or not length:
            return
        try:
            with open(full, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # a browser closing a tab mid-download/video is normal; don't print a traceback for it
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="Browse Sadaqah-e-Jariyah locally.")
    ap.add_argument("port", nargs="?", type=int, default=8000)
    ap.add_argument("--lan", action="store_true", help="listen on all interfaces (share on local Wi-Fi)")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    a = ap.parse_args()

    idx = build_index.build(ROOT)
    build_index.write(idx)
    n, size = build_index.stats(idx["root"])

    host = "0.0.0.0" if a.lan else "127.0.0.1"
    server = None
    for port in range(a.port, a.port + 20):
        try:
            server = Server((host, port), Handler)
            break
        except OSError:
            continue
    if not server:
        sys.exit(f"No free port between {a.port} and {a.port + 19}.")
    server.daemon_threads = True

    url = f"http://localhost:{port}/"
    print(f"\n  ◉ Sadaqah-e-Jariyah\n  {n:,} files · {size / 1e9:.1f} GB indexed\n\n  → {url}")
    if a.lan and lan_ip():
        print(f"  → http://{lan_ip()}:{port}/  (same Wi-Fi)")
    print("\n  Ctrl+C to stop.\n")
    if not a.no_open:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
