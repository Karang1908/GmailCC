#!/usr/bin/env python3
"""A stand-in for the n8n webhook, so the whole pipeline can be tested without n8n.

It performs the same secret check and returns the same shape of response as the
shipped workflow, then writes the rendered HTML to a file you can open in a
browser to see exactly what the client would receive.

    python3 tools/mock_webhook.py --port 8787 --secret test-secret --out /tmp/preview

Point config.json's webhook_url at http://127.0.0.1:8787/webhook/clientmail-send.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SECRET = ""
OUTDIR = Path("/tmp/clientmail-preview")


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "body was not JSON"})
            return

        if SECRET and data.get("secret") != SECRET:
            self._json(401, {"ok": False, "error": "bad secret"})
            return

        if data.get("probe"):
            self._json(200, {"ok": True, "probe": True})
            return

        missing = [f for f in ("from", "to", "subject", "html") if not data.get(f)]
        if missing:
            self._json(400, {"ok": False, "error": f"missing: {', '.join(missing)}"})
            return

        OUTDIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", data["subject"].lower())[:40].strip("-")
        html_file = OUTDIR / f"{stamp}-{slug}.html"
        html_file.write_text(data["html"], encoding="utf-8")
        (OUTDIR / f"{stamp}-{slug}.txt").write_text(data.get("text", ""), encoding="utf-8")

        # Decode attachments back to real files. If a screenshot survives this
        # round trip byte-for-byte, the payload is correct and anything that goes
        # wrong afterwards is n8n's binary handling, not ours.
        written = []
        for att in data.get("attachments") or []:
            try:
                raw = base64.b64decode(att["data"])
            except (KeyError, ValueError, TypeError) as exc:
                self._json(400, {"ok": False, "error": f"bad attachment encoding: {exc}"})
                return
            dest = OUTDIR / f"{stamp}-{att.get('fileName', 'attachment.bin')}"
            dest.write_bytes(raw)
            written.append(f"{dest.name} ({len(raw)} bytes, {att.get('mimeType')})")

        print(f"\n--- MOCK SEND ---------------------------------------")
        print(f"  from:    {data['from']}")
        print(f"  to:      {data['to']}")
        print(f"  cc:      {data.get('cc') or '(none)'}")
        print(f"  bcc:     {data.get('bcc') or '(none)'}")
        print(f"  subject: {data['subject']}")
        print(f"  from:    {data.get('fromName') or '(gmail default)'}")
        print(f"  html:    {len(data['html'])} bytes -> {html_file}")
        print(f"  text:    {len(data.get('text', ''))} bytes")
        for w in written:
            print(f"  attach:  {w}")
        print(f"-----------------------------------------------------\n")

        self._json(200, {"ok": True, "messageId": f"mock-{stamp}", "threadId": f"mockthread-{stamp}"})

    def log_message(self, *args) -> None:  # silence per-request noise
        pass


def main() -> int:
    global SECRET, OUTDIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--secret", default="")
    ap.add_argument("--out", default=str(OUTDIR))
    args = ap.parse_args()

    SECRET = args.secret
    OUTDIR = Path(args.out)
    print(f"mock n8n webhook on http://127.0.0.1:{args.port}/webhook/clientmail-send")
    print(f"previews -> {OUTDIR}")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
