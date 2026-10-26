"""A tiny fake Ollama server for tests (no model needed)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE_TRANSCRIPT = """## NOTICE OF LEASE RENEWAL

Date: 14 March 2025

To: Sunrise Traders Pvt Ltd, Kolkata

This is to inform you that the lease for Unit 4B expires on 31 March 2025. The landlord proposes renewal on the terms below.

| Term | Current | Proposed |
|---|---|---|
| Monthly rent | 45,000 | 49,500 |
| Deposit | 1,35,000 | 1,50,000 |
| Lock-in | 12 months | 24 months |

Please confirm acceptance within 15 days."""

FIGURE_JSON = {
    "kind": "bar_chart",
    "title": "Revenue vs Costs FY2025",
    "summary": "Grouped bar chart of revenue and costs (INR crore) for Q1-Q4. Revenue rises from 120 to 172; costs from 80 to 99.",
    "data_table": "| Quarter | Revenue (INR cr) | Costs (INR cr) |\n|---|---|---|\n| Q1 | 120 | 80 |\n| Q2 | 135 | 85 |\n| Q3 | 150 | 92 |\n| Q4 | 172 | 99 |",
    "text_in_image": "Revenue vs Costs FY2025; INR crore; Revenue; Costs",
}


class MockOllama:
    def __init__(self, models=("qwen3-vl:8b-instruct",), page_text: str = PAGE_TRANSCRIPT):
        self.models = list(models)
        self.page_text = page_text
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/version":
                    return self._send({"version": "0.13.0-mock"})
                if self.path == "/api/tags":
                    return self._send({"models": [{"name": m} for m in outer.models]})
                self._send({"error": "not found"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append(body)
                if self.path != "/api/chat":
                    return self._send({"error": "not found"}, 404)
                if body.get("model") not in outer.models:
                    return self._send({"error": f"model '{body.get('model')}' not found"}, 404)
                if body.get("format"):
                    content = json.dumps(FIGURE_JSON)
                else:
                    content = outer.page_text
                self._send({"message": {"role": "assistant", "content": content}, "done": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
