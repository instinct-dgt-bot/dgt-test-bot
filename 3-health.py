"""Servidor HTTP mínimo para Render /health; evita exponer tokens y datos."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok\n")
    def log_message(self, fmt, *args):
        pass


def main():
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), HealthHandler)
    server.serve_forever()


if __name__ == '__main__':
    main()
