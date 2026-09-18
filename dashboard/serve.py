#!/usr/bin/env python3
"""Simple HTTP server for UVO Vestnik dashboard."""

import json
import glob
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8080
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "results")
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/data":
            self.send_api_data()
        else:
            super().do_GET()

    def send_api_data(self):
        all_docs = []
        pattern = os.path.join(DATA_DIR, "vestnik_*_parser_results.json")
        for filepath in sorted(glob.glob(pattern)):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    docs = json.load(f)
                    all_docs.extend(docs)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error reading {filepath}: {e}")

        response = json.dumps(all_docs, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response)


if __name__ == "__main__":
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"UVO Vestnik Dashboard: http://localhost:{PORT}")
    print(f"API endpoint: http://localhost:{PORT}/api/data")
    print(f"Data directory: {DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
