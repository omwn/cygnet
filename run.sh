#!/usr/bin/env bash
# Usage: run.sh [dir]  — serves dir (default: web/) in a browser.
cd "${1:-$(dirname "$0")/web}"
python3 - <<'EOF'
import http.server, socket, webbrowser

class Handler(http.server.SimpleHTTPRequestHandler):
    """Serve .gz files as raw bytes so the browser doesn't auto-decompress them."""
    def guess_type(self, path):
        if str(path).endswith('.gz'):
            return 'application/octet-stream'
        return super().guess_type(path)

def find_free_port(start=8801):
    for port in range(start, 65535):
        with socket.socket() as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                continue

import getpass, socket as _socket
port = find_free_port()
user = getpass.getuser()
host = _socket.gethostname()
try:
    host = _socket.gethostbyname(host)
except Exception:
    pass
print(f"Serving at http://localhost:{port}")
print(f"To access remotely: ssh -L {port}:localhost:{port} {user}@{host}")
webbrowser.open(f"http://localhost:{port}")
http.server.test(HandlerClass=Handler, port=port, bind="0.0.0.0")
EOF
