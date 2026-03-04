#!/bin/bash
cd "$(dirname "$0")/web"
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

port = find_free_port()
print(f"Serving at http://localhost:{port}")
webbrowser.open(f"http://localhost:{port}")
http.server.test(HandlerClass=Handler, port=port, bind="0.0.0.0")
EOF
