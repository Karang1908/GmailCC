#!/usr/bin/env python3
"""End-to-end test: drives the MCP server over real stdio JSON-RPC.

Runs against a throwaway CLIENTMAIL_HOME and the mock webhook, so it never
touches your real config or sends real mail. Exercised paths include every
refusal the safety gates are supposed to produce.

    python3 tools/selftest.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

PASS, FAIL = "  PASS", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label))
    print(f"{PASS if ok else FAIL}  {label}")
    if not ok and detail:
        print(f"        {detail}")


class Server:
    """Talks JSON-RPC to clientmail_server.py over a pipe, like Claude Code does."""

    def __init__(self, home: Path):
        env = {**os.environ, "CLIENTMAIL_HOME": str(home), "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server" / "clientmail_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, bufsize=1,
        )
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"server died. stderr:\n{self.proc.stderr.read()}")
        return json.loads(line)

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def tool(self, name: str, args: dict) -> tuple[dict, bool]:
        resp = self.call("tools/call", {"name": name, "arguments": args})
        result = resp["result"]
        return json.loads(result["content"][0]["text"]), result.get("isError", False)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def start_mock(port: int, secret: str, out: Path) -> HTTPServer:
    import mock_webhook
    mock_webhook.SECRET = secret
    mock_webhook.OUTDIR = out
    httpd = HTTPServer(("127.0.0.1", port), mock_webhook.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    (path / "search.py").write_text("def search(q):\n    return exact_match(q)\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "initial")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="clientmail-test-"))
    home, repo, preview = tmp / "home", tmp / "repo", tmp / "preview"
    port, secret = 8799, "s3cr3t"

    home.mkdir(parents=True)
    shutil.copytree(ROOT / "templates", home / "templates")
    make_repo(repo)
    start_mock(port, secret, preview)

    (home / "config.json").write_text(json.dumps({
        "webhook_url": f"http://127.0.0.1:{port}/webhook/clientmail-send",
        "webhook_secret": secret,
        "from_name": "Karan Garg",
        "brand": {"name": "Studio", "color": "#2563eb", "signoff": "— Karan", "site": "https://example.com"},
        "paused": False,
        "allowed_recipients": ["@acme.com"],
        "clients": {"acme": {"name": "Jane", "email": "jane@acme.com"}},
    }, indent=2))

    s = Server(home)
    print("\n== protocol ==")
    init = s.call("initialize", {"protocolVersion": "2025-06-18",
                                 "capabilities": {}, "clientInfo": {"name": "selftest", "version": "1"}})
    check(init.get("result", {}).get("serverInfo", {}).get("name") == "clientmail",
          "initialize returns serverInfo", json.dumps(init)[:300])
    check(init["result"]["protocolVersion"] == "2025-06-18",
          "initialize echoes the client's protocol version")
    s.notify("notifications/initialized")
    check(s.call("ping")["result"] == {}, "ping answers empty result")

    tools = s.call("tools/list")["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    check(names == ["config_check", "draft_render", "email_send",
                    "templates_list", "work_start", "work_status"],
          f"tools/list exposes all 6 tools ({', '.join(names)})")
    check(all("inputSchema" in t and "description" in t for t in tools),
          "every tool has a schema and description")

    bad = s.call("tools/call", {"name": "nope", "arguments": {}})
    check("error" in bad, "unknown tool -> JSON-RPC error")
    unknown = s.call("does/not/exist")
    check(unknown.get("error", {}).get("code") == -32601, "unknown method -> -32601")

    print("\n== config ==")
    cfg, err = s.tool("config_check", {})
    check(cfg["ok"] and not err, "config_check passes on a valid config", json.dumps(cfg)[:400])
    check(sorted(cfg["templates"]) == ["client-update", "plain"],
          f"both templates found: {cfg['templates']}")

    print("\n== work tracking ==")
    st, err = s.tool("work_status", {"repo_path": str(repo)})
    check(not st["ok"], "work_status without a baseline explains itself, does not crash")

    ws, err = s.tool("work_start", {
        "repo_path": str(repo), "client": "acme",
        "request": "Search misses partial words and the page freezes when typing.",
        "title": "search fixes"})
    check(ws["ok"] and len(ws["baseline"]) == 40, "work_start pins a 40-char baseline SHA")
    check(ws["client"]["email"] == "jane@acme.com", "client key resolved from config")

    # Work happens: one commit plus an uncommitted edit plus an untracked file.
    (repo / "search.py").write_text("def search(q):\n    return fuzzy_match(q, debounce=200)\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "fix: fuzzy search + debounce"], cwd=repo, capture_output=True)
    (repo / "search.py").write_text("def search(q):\n    return fuzzy_match(q, debounce=150)\n")
    (repo / "NOTES.md").write_text("scratch\n")

    st, err = s.tool("work_status", {"repo_path": str(repo)})
    check(st["ok"], "work_status returns evidence")
    check(len(st["changes"]["commits"]) == 1, "commit since baseline is reported")
    check(any(f["path"] == "search.py" for f in st["changes"]["files_changed"]),
          "UNCOMMITTED working-tree change is included in the evidence")
    check("NOTES.md" in st["changes"]["untracked"], "untracked file is reported")
    check(st["request"].startswith("Search misses"), "original request returned verbatim")

    print("\n== drafting ==")
    drafts_dir = Path(st["drafts_dir"])
    drafts_dir.mkdir(parents=True, exist_ok=True)
    draft = drafts_dir / "2026-08-03-acme-search.md"
    draft.write_text(
        "---\n"
        "to: jane@acme.com\n"
        "cc: ops@acme.com\n"
        "subject: Search fixes are live\n"
        "template: client-update\n"
        "client: acme\n"
        "---\n\n"
        "Hi Jane,\n\n"
        "The search problems you reported are fixed and live.\n\n"
        "## What's different\n\n"
        "- **Partial words** — searching \"invo\" used to return nothing; it now finds "
        "*Invoice* and **Invoicing** straight away.\n"
        "- **Typing lag** — the page froze while you typed; it now keeps up. "
        "See [the page](https://example.com/search).\n\n"
        "Nothing needed from you.\n",
        encoding="utf-8")

    r, err = s.tool("draft_render", {"draft_path": str(draft)})
    check(r["ok"] and not err, "draft_render succeeds", json.dumps(r)[:400])
    good_hash = r["hash"]
    check(r["cc"] == ["ops@acme.com"], "cc parsed into a list")
    html_file = preview / "render-check.html"
    check("<strong" in r["text_preview"] or "**" not in r["text_preview"],
          "markdown markers stripped from the text part")
    check("Invoice" in r["text_preview"], "body content present in text part")

    print("\n== safety gates ==")
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": "deadbeef",
                                 "confirm_recipient": "jane@acme.com"})
    check(e, "stale hash is refused")

    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": good_hash,
                                 "confirm_recipient": "someone@else.com"})
    check(e, "recipient mismatch is refused")

    dry, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": good_hash,
                                   "confirm_recipient": "jane@acme.com", "dry_run": True})
    check(not e and dry["sent"] is False, "dry_run builds the payload without sending")

    # Allowlist: swap in an outside recipient.
    outside = drafts_dir / "outside.md"
    outside.write_text(draft.read_text().replace("jane@acme.com", "stranger@evil.com")
                                        .replace("cc: ops@acme.com", "cc:"), encoding="utf-8")
    o = json.loads(json.dumps(s.tool("draft_render", {"draft_path": str(outside)})[0]))
    _, e = s.tool("email_send", {"draft_path": str(outside), "confirm_hash": o["hash"],
                                 "confirm_recipient": "stranger@evil.com"})
    check(e, "recipient outside allowed_recipients is refused")

    # Paused.
    cfg_file = home / "config.json"
    original = cfg_file.read_text()
    cfg_file.write_text(original.replace('"paused": false', '"paused": true'))
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": good_hash,
                                 "confirm_recipient": "jane@acme.com"})
    check(e, "paused: true blocks sending")
    cfg_file.write_text(original)

    print("\n== real send (to the mock) ==")
    sent, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": good_hash,
                                    "confirm_recipient": "jane@acme.com"})
    check(not e and sent.get("sent") is True, "send succeeds end to end", json.dumps(sent)[:400])
    check(sent.get("n8n_status") == 200, "webhook returned 200")
    previews = list(preview.glob("*.html"))
    check(bool(previews), "webhook received and stored the rendered HTML")
    if previews:
        body = previews[0].read_text()
        check("<strong style=" in body, "bold rendered with an inline style")
        check("<li style=" in body, "bullets rendered as styled <li>")
        check('<a href="https://example.com/search"' in body, "link rendered")
        check("{{" not in body, "no unfilled {{ placeholders }} leaked into the email")
        check("<em>" in body, "italic rendered")
    log = (home / "sent.log")
    check(log.exists() and "jane@acme.com" in log.read_text(), "send appended to sent.log")

    # Bad secret path.
    cfg_file.write_text(original.replace(secret, "wrong-secret"))
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": good_hash,
                                 "confirm_recipient": "jane@acme.com"})
    check(e, "wrong webhook_secret surfaces as an error, not a silent success")
    cfg_file.write_text(original)

    print("\n== malformed drafts ==")
    for label, body in [
        ("no frontmatter", "Hi Jane,\n"),
        ("unclosed frontmatter", "---\nto: a@b.com\nsubject: x\n"),
        ("missing to:", "---\nsubject: x\n---\n\nbody\n"),
        ("missing subject:", "---\nto: a@b.com\n---\n\nbody\n"),
        ("empty body", "---\nto: a@b.com\nsubject: x\n---\n\n"),
        ("unknown field", "---\nto: a@b.com\nsubject: x\nfrom: me\n---\n\nbody\n"),
        ("bad template name", "---\nto: a@b.com\nsubject: x\ntemplate: nope\n---\n\nbody\n"),
    ]:
        p = drafts_dir / f"bad-{label.replace(' ', '-')}.md"
        p.write_text(body, encoding="utf-8")
        out, e = s.tool("draft_render", {"draft_path": str(p)})
        check(e and "error" in out, f"rejected with a clear message: {label}",
              json.dumps(out)[:200])

    out, e = s.tool("draft_render", {"draft_path": str(drafts_dir / "nothere.md")})
    check(e, "missing draft file reports cleanly")

    s.close()

    print("\n" + "=" * 55)
    failed = [label for ok, label in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("\nFAILED:")
        for label in failed:
            print(f"  - {label}")
    print(f"\nartifacts: {tmp}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
