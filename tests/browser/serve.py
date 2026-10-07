"""Tiny static server for the local browser checks (tests/browser/process.html).

Usage:  py tests\\browser\\serve.py --port 8000      (from anywhere; it always serves the repository root)

Why not `py -m http.server`: on some Windows PCs its listen backlog of 5 makes browsers drop
image connections, and the harness results (base64 of multi-MB JPEGs) are too big to read out
through a browser tool. This server
  - binds 127.0.0.1 only and answers only requests addressed to localhost / 127.0.0.1,
  - handles each connection in its own thread, with a listen backlog of 64 and HTTP/1.1 keep-alive,
  - serves the repository root read-only (never cached, so edits show on reload),
  - and accepts exactly one kind of write: PUT /tests/browser/out/<file> saves the request body
    (up to 50 MB) as tests/browser/out/<file>. Nothing else can be written.
It is not part of the published site: build.py --out copies only photos/ and admin/.
"""
import argparse
import os
import re
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "tests" / "browser" / "out"
OUT_URL = "/tests/browser/out/"
MAX_PUT = 50 * 1024 * 1024  # bytes
FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")  # one plain file name; no slash, no leading dot
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: every response below carries a Content-Length
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".mjs": "text/javascript"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # --- reading ---------------------------------------------------------------------------

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self._local():
            super().do_GET()

    def do_HEAD(self):
        if self._local():
            super().do_HEAD()

    def _local(self):
        """False (after answering 403) when the request names another host: a web page using DNS rebinding."""
        if self.headers.get("Host", "").rsplit(":", 1)[0] in LOCAL_HOSTS:
            return True
        self.send_error(403, "Only localhost is served")
        return False

    # --- writing ---------------------------------------------------------------------------

    def do_PUT(self):
        if not self._local():
            return
        target = self._put_target()
        if target is None:
            return self._refuse(400, "PUT is only accepted at /tests/browser/out/<file>")
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self._refuse(411, "Content-Length is required")
        try:
            size = int(self.headers["Content-Length"])
        except (TypeError, ValueError):
            return self._refuse(411, "Content-Length is required")
        if size < 0:
            return self._refuse(400, "Bad Content-Length")
        if size > MAX_PUT:
            return self._refuse(413, f"Body is larger than {MAX_PUT} bytes")

        body = self.rfile.read(size)
        if len(body) != size:
            self.close_connection = True
            return
        OUT.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=OUT, prefix=".put-")
        try:
            with os.fdopen(handle, "wb") as f:
                f.write(body)
            os.replace(temp, target)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _put_target(self):
        """tests/browser/out/<file> for a request path of that exact shape, else None."""
        path = unquote(urlsplit(self.path).path)
        if not path.startswith(OUT_URL):
            return None
        name = path[len(OUT_URL):]
        if not FILE_NAME.fullmatch(name) or ".." in name:
            return None
        return OUT / name

    def _refuse(self, status, message):
        """Answer an error without reading the body, so the connection cannot be reused."""
        self.close_connection = True
        self.send_error(status, message)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    # On Windows SO_REUSEADDR lets a second server share a port that is already in use, silently.
    allow_reuse_address = os.name != "nt"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with Server(("127.0.0.1", args.port), Handler) as server:
        print(f"Serving {ROOT} at http://127.0.0.1:{args.port}/ (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
