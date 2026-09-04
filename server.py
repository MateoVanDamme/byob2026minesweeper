#!/usr/bin/env python3
"""QR Minesweeper.

A minesweeper board meant to be projected on a wall. Every hidden tile shows a
QR code. Scanning a code opens /t/<id> on the phone, which reveals that tile.

Stdlib only, except the `qrcode` package for QR code SVGs.

    python server.py --cols 10 --rows 6 --mines 10
"""
import argparse
import json
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import qrcode
import qrcode.image.svg

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
}


# ----------------------------------------------------------------------------
# Game state
# ----------------------------------------------------------------------------
class Game:
    def __init__(self, cols, rows, mine_count):
        self.cols = cols
        self.rows = rows
        self.mine_count = max(1, min(mine_count, cols * rows - 9))
        self.lock = threading.Lock()
        self.version = 0
        self.games_played = 0
        self._reset_unlocked()

    # -- helpers --------------------------------------------------------------
    @property
    def size(self):
        return self.cols * self.rows

    def in_range(self, i):
        return 0 <= i < self.size

    def coord(self, i):
        r, c = divmod(i, self.cols)
        return f"{chr(65 + r)}{c + 1}"

    def neighbors(self, i):
        r, c = divmod(i, self.cols)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r + dr, c + dc
                if 0 <= rr < self.rows and 0 <= cc < self.cols:
                    yield rr * self.cols + cc

    def count(self, i):
        return sum(1 for n in self.neighbors(i) if n in self.mines)

    def _bump(self, event=None):
        self.version += 1
        self.last_event = event

    # -- actions --------------------------------------------------------------
    def _reset_unlocked(self):
        self.mines = set()
        self.revealed = set()
        self.flags = set()
        self.exploded = None
        self.status = "ready"  # ready | playing | won | lost
        self.started_at = None
        self.ended_at = None
        self.last_event = None
        self._bump()

    def reset(self):
        with self.lock:
            self._reset_unlocked()

    def _place_mines(self, safe):
        # First scan is always safe, and always opens an area.
        forbidden = {safe, *self.neighbors(safe)}
        pool = [i for i in range(self.size) if i not in forbidden]
        self.mines = set(random.sample(pool, min(self.mine_count, len(pool))))

    def reveal(self, i):
        with self.lock:
            base = {"id": i, "coord": self.coord(i), "status": self.status}
            if self.status in ("won", "lost"):
                return {**base, "result": "over"}
            if i in self.revealed:
                return {**base, "result": "already", "count": self.count(i)}

            if self.status == "ready":
                self._place_mines(i)
                self.status = "playing"
                self.started_at = time.time()
                self.games_played += 1

            self.flags.discard(i)

            if i in self.mines:
                self.revealed.add(i)
                self.exploded = i
                self.status = "lost"
                self.ended_at = time.time()
                self._bump({"type": "boom", "id": i})
                return {**base, "result": "boom", "status": self.status}

            # flood fill from zero tiles
            opened = []
            stack = [i]
            while stack:
                t = stack.pop()
                if t in self.revealed:
                    continue
                self.revealed.add(t)
                self.flags.discard(t)
                opened.append(t)
                if self.count(t) == 0:
                    stack.extend(n for n in self.neighbors(t) if n not in self.revealed)

            if len(self.revealed) == self.size - len(self.mines):
                self.status = "won"
                self.ended_at = time.time()
                self._bump({"type": "win", "id": i})
                return {**base, "result": "win", "count": self.count(i),
                        "opened": len(opened), "status": self.status}

            self._bump({"type": "open", "id": i, "opened": opened})
            return {**base, "result": "ok", "count": self.count(i),
                    "opened": len(opened), "status": self.status}

    def tile_info(self, i):
        with self.lock:
            over = self.status in ("won", "lost")
            if i in self.revealed:
                state = "mine" if i in self.mines else "open"
            elif i in self.flags:
                state = "flagged"
            else:
                state = "hidden"
            return {
                "id": i, "coord": self.coord(i), "status": self.status,
                "state": state, "over": over,
                "count": self.count(i) if state == "open" else None,
            }

    def toggle_flag(self, i):
        with self.lock:
            if self.status in ("won", "lost") or i in self.revealed:
                return {"id": i, "flagged": i in self.flags, "changed": False}
            if i in self.flags:
                self.flags.discard(i)
            else:
                self.flags.add(i)
            self._bump({"type": "flag", "id": i})
            return {"id": i, "flagged": i in self.flags, "changed": True}

    # -- serialisation --------------------------------------------------------
    def state(self):
        """Tile codes: h hidden, f flag, 0-8 open, m mine, x exploded, w wrong flag."""
        with self.lock:
            tiles = []
            over = self.status in ("won", "lost")
            for i in range(self.size):
                if i in self.revealed:
                    if i in self.mines:
                        tiles.append("x" if i == self.exploded else "m")
                    else:
                        tiles.append(str(self.count(i)))
                elif over and i in self.mines:
                    tiles.append("f" if (self.status == "won" or i in self.flags) else "m")
                elif i in self.flags:
                    tiles.append("w" if over else "f")
                else:
                    tiles.append("h")

            if self.started_at is None:
                elapsed = 0
            else:
                elapsed = (self.ended_at or time.time()) - self.started_at

            return {
                "version": self.version,
                "cols": self.cols,
                "rows": self.rows,
                "mines": self.mine_count,
                "flags": len(self.flags),
                "revealed": len(self.revealed),
                "status": self.status,
                "elapsed": round(elapsed, 1),
                "games": self.games_played,
                "tiles": tiles,
                "event": self.last_event,
            }


# ----------------------------------------------------------------------------
# QR codes
# ----------------------------------------------------------------------------
class QRCache:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self._cache = {}
        self._lock = threading.Lock()

    def url_for(self, i):
        return f"{self.base_url}/t/{i}"

    def svg(self, i):
        with self._lock:
            if i in self._cache:
                return self._cache[i]
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=0,
        )
        qr.add_data(self.url_for(i))
        qr.make(fit=True)
        img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        svg = img.to_string().decode("utf-8")
        # Let CSS size it, and keep module edges crisp when scaled up.
        n = qr.modules_count
        svg = svg.replace(f'width="{n}mm" height="{n}mm" ', "", 1)
        svg = svg.replace("<svg ", '<svg shape-rendering="crispEdges" ', 1)
        data = svg.encode("utf-8")
        with self._lock:
            self._cache[i] = data
        return data


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def make_handler(game: Game, qrs: QRCache):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QRMinesweeper/1.0"

        def log_message(self, fmt, *args):
            # the projector polls state constantly; keep the log readable
            if self.path.startswith("/api/state"):
                return
            super().log_message(fmt, *args)

        # -- helpers ------------------------------------------------------------
        def send_bytes(self, status, body, ctype, cache=False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",
                             "public, max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, obj, status=200):
            self.send_bytes(status, json.dumps(obj).encode("utf-8"), MIME[".json"])

        def send_file(self, rel):
            path = (STATIC / rel).resolve()
            if STATIC not in path.parents or not path.is_file():
                return self.send_json({"error": "not found"}, 404)
            ctype = MIME.get(path.suffix, "application/octet-stream")
            self.send_bytes(200, path.read_bytes(), ctype)

        def tile_id(self, s):
            try:
                i = int(s)
            except ValueError:
                return None
            return i if game.in_range(i) else None

        # -- routes -------------------------------------------------------------
        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/") or "/"
            parts = path.lower().split("/")[1:]

            if path == "/":
                return self.send_file("index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self.send_file(parts[1])
            if parts[0] == "t" and len(parts) == 2:
                # phone landing page; the page itself POSTs the reveal
                return self.send_file("tile.html")
            if parts[0] == "qr" and len(parts) == 2:
                i = self.tile_id(parts[1].removesuffix(".svg"))
                if i is None:
                    return self.send_json({"error": "bad tile"}, 404)
                return self.send_bytes(200, qrs.svg(i), MIME[".svg"], cache=True)
            if parts[0] == "api":
                if len(parts) == 3 and parts[1] == "tile":
                    i = self.tile_id(parts[2])
                    if i is None:
                        return self.send_json({"error": "bad tile"}, 404)
                    return self.send_json(game.tile_info(i))
                if parts[1:] == ["state"]:
                    return self.send_json(game.state())
                if parts[1:] == ["config"]:
                    return self.send_json({
                        "cols": game.cols, "rows": game.rows,
                        "mines": game.mine_count, "base_url": qrs.base_url,
                    })
            self.send_json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            parts = path.lower().split("/")[1:]
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)

            if parts[:1] != ["api"]:
                return self.send_json({"error": "not found"}, 404)
            if parts[1:] == ["reset"]:
                game.reset()
                return self.send_json({"ok": True})
            if len(parts) == 3 and parts[1] in ("reveal", "flag"):
                i = self.tile_id(parts[2])
                if i is None:
                    return self.send_json({"error": "bad tile"}, 404)
                if parts[1] == "reveal":
                    return self.send_json(game.reveal(i))
                return self.send_json(game.toggle_flag(i))
            self.send_json({"error": "not found"}, 404)

    return Handler


def lan_ip():
    """Best-effort guess of the LAN address phones can reach."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description="QR Minesweeper server")
    ap.add_argument("--cols", type=int, default=10)
    ap.add_argument("--rows", type=int, default=6)
    ap.add_argument("--mines", type=int, default=10)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default=None,
                    help="address/hostname phones use to reach this machine "
                         "(default: auto-detected LAN IP)")
    ap.add_argument("--base-url", default=None,
                    help="full base URL to encode in the QR codes, e.g. "
                         "http://mines.local:8000 (overrides --host/--port)")
    args = ap.parse_args()

    host = args.host or lan_ip()
    base_url = args.base_url or f"http://{host}:{args.port}"

    game = Game(args.cols, args.rows, args.mines)
    qrs = QRCache(base_url)

    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(game, qrs))
    httpd.daemon_threads = True

    print(f"board      : {game.cols} x {game.rows}, {game.mine_count} mines")
    print(f"projector  : http://localhost:{args.port}/")
    print(f"qr codes   : {base_url}/t/<id>")
    print("ctrl+c to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
