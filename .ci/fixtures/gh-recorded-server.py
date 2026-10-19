#!/usr/bin/env python3
"""Serve recorded GitHub replies on loopback for the length of one command.

Usage: gh-recorded-server.py REPLIES [--log FILE] -- COMMAND...

REPLIES is a JSON object mapping a request path, query included, to
{"status": 200, "body": ..., "headers": {...}}, where status and headers are
optional. The command runs with GH_RECORDED_API set to the server's origin and
this exits with its status. A path REPLIES lacks answers 404 as GitHub does,
and any method but GET answers 405, so a test reads a write attempt from the
log, one JSON line per request: {"method", "path", "authorization"}.

It stands in for api.github.com so that `run/fakes.py compare` is proved
offline; the command, not a background process, owns the server's lifetime.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

args = sys.argv[1:]
if "--" not in args or args.index("--") == 0:
    sys.exit(__doc__)
split = args.index("--")
own, command = args[:split], args[split + 1 :]
replies = json.loads(Path(own[0]).read_text())
log = Path(own[own.index("--log") + 1]) if "--log" in own else None
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def answer(self):
        if log:
            line = {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
            with lock, log.open("a") as f:
                f.write(json.dumps(line) + "\n")
        if self.command != "GET":
            reply = {"status": 405, "body": {"message": "Method Not Allowed"}}
        else:
            reply = replies.get(
                self.path, {"status": 404, "body": {"message": "Not Found"}}
            )
        body = reply["body"]
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(reply.get("status", 200))
        for name, value in reply.get("headers", {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = answer

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {**os.environ, "GH_RECORDED_API": f"http://127.0.0.1:{server.server_port}"}
try:
    sys.exit(subprocess.run(command, env=env).returncode)
finally:
    server.shutdown()
