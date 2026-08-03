#!/usr/bin/env python3
"""End-to-end test: drives the MCP server over real stdio JSON-RPC.

Runs against a throwaway CLIENTMAIL_HOME, a throwaway HOME (for session
transcripts) and the mock webhook, so it never touches your real config and
never sends real mail. Every refusal the safety gates are supposed to produce
is exercised, because those are the paths that matter most and the ones nobody
tests by hand.

    python3 tools/selftest.py
"""

from __future__ import annotations

import base64
import json
import os
import re
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

# A genuinely valid 1x1 PNG, so attachment handling is tested on real image bytes.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((bool(ok), label))
    print(f"{'  PASS' if ok else '  FAIL'}  {label}")
    if not ok and detail:
        print(f"        {detail}")


class Server:
    """Talks JSON-RPC to clientmail_server.py over a pipe, like Claude Code does."""

    def __init__(self, home: Path, fake_home: Path):
        env = {**os.environ, "CLIENTMAIL_HOME": str(home),
               "HOME": str(fake_home), "PYTHONUNBUFFERED": "1"}
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
        result = self.call("tools/call", {"name": name, "arguments": args})["result"]
        return json.loads(result["content"][0]["text"]), result.get("isError", False)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def start_mock(port: int, secret: str, out: Path) -> None:
    import mock_webhook
    mock_webhook.SECRET = secret
    mock_webhook.OUTDIR = out
    httpd = HTTPServer(("127.0.0.1", port), mock_webhook.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    (path / "search.py").write_text("def search(q):\n    return exact_match(q)\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "initial")


def make_transcript(fake_home: Path, cwd: Path) -> Path:
    """A synthetic transcript with the quirks the real format has: prompts
    repeated by session bridging, harness markers, sidechain noise, tool calls."""
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd.resolve()))
    d = fake_home / ".claude" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / "11111111-2222-3333-4444-555555555555.jsonl"

    def user(text, ts):
        return {"type": "user", "timestamp": ts, "cwd": str(cwd), "gitBranch": "main",
                "sessionId": "11111111-2222-3333-4444-555555555555",
                "message": {"role": "user", "content": text}}

    def assistant(blocks, ts):
        return {"type": "assistant", "timestamp": ts, "cwd": str(cwd),
                "message": {"role": "assistant", "content": blocks}}

    rows = [
        user("Search is broken, it returns nothing for partial words.", "2026-08-03T10:00:00.000Z"),
        user("Search is broken, it returns nothing for partial words.", "2026-08-03T10:00:00.000Z"),
        user("[Request interrupted by user]", "2026-08-03T10:00:30.000Z"),
        assistant([{"type": "text", "text": "I'll fix the matcher."},
                   {"type": "tool_use", "id": "t1", "name": "Write",
                    "input": {"file_path": str(cwd / "search.py")}}], "2026-08-03T10:01:00.000Z"),
        assistant([{"type": "tool_use", "id": "t2", "name": "Bash",
                    "input": {"command": "pytest -q", "description": "Run tests"}}],
                  "2026-08-03T10:02:00.000Z"),
        {"type": "user", "timestamp": "2026-08-03T10:02:05.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
              "content": "2 tests failed"}]}},
        # Sidechain noise must not appear in the digest.
        {"type": "user", "isSidechain": True, "timestamp": "2026-08-03T10:02:10.000Z",
         "message": {"role": "user", "content": "subagent internal chatter"}},
        user("also make it debounce while typing\n<system-reminder>ignore me</system-reminder>",
             "2026-08-03T10:03:00.000Z"),
        assistant([{"type": "tool_use", "id": "t3", "name": "Edit",
                    "input": {"file_path": str(cwd / "ui.py")}}], "2026-08-03T10:04:00.000Z"),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def write_draft(path: Path, **kw) -> None:
    fm = {"to": "", "cc": "", "bcc": "", "subject": "Search fixes are live",
          "template": "client-update", "attachments": ""}
    fm.update(kw)
    body = kw.pop("body", None) or (
        "Hi Jane,\n\nThe search problems you reported are fixed and live.\n\n"
        "## What's different\n\n"
        "- **Partial words** — searching \"invo\" used to return nothing; it now finds "
        "*Invoice* and **Invoicing** straight away.\n"
        "- **Typing lag** — the page froze while you typed; it keeps up now. "
        "See [the page](https://example.com/search).\n\n"
        "Nothing needed from you.\n")
    fm.pop("body", None)
    lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    path.write_text(f"---\n{lines}\n---\n\n{body}", encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="clientmail-test-"))
    home, fake_home, repo, preview = tmp / "home", tmp / "fakehome", tmp / "repo", tmp / "preview"
    assets = tmp / "assets"
    port, secret = 8799, "s3cr3t"

    home.mkdir(parents=True)
    (fake_home).mkdir(parents=True)
    assets.mkdir(parents=True)
    shutil.copytree(ROOT / "templates", home / "templates")
    make_repo(repo)
    make_transcript(fake_home, repo)
    start_mock(port, secret, preview)

    (assets / "before.png").write_bytes(TINY_PNG)
    (assets / "after.png").write_bytes(TINY_PNG)
    (assets / "notes.txt").write_text("a note", encoding="utf-8")
    (assets / "danger.exe").write_bytes(b"MZ\x00\x00")
    (assets / "empty.png").write_bytes(b"")

    (home / "config.json").write_text(json.dumps({
        "webhook_url": f"http://127.0.0.1:{port}/webhook/clientmail-send",
        "webhook_secret": secret,
        "from_email": "karan@example.com",
        "from_name": "Karan Garg",
        "brand": {"name": "Studio", "color": "#2563eb", "signoff": "— Karan",
                  "site": "https://example.com"},
        "paused": False,
        "allowed_recipients": ["@acme.com"],
        "clients": {"acme": {"name": "Jane", "email": "jane@acme.com"}},
    }, indent=2))

    s = Server(home, fake_home)

    print("\n== protocol ==")
    init = s.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "selftest", "version": "1"}})
    check(init.get("result", {}).get("serverInfo", {}).get("name") == "clientmail",
          "initialize returns serverInfo", json.dumps(init)[:300])
    check(init["result"]["protocolVersion"] == "2025-06-18",
          "initialize echoes the client's protocol version")
    s.notify("notifications/initialized")
    check(s.call("ping")["result"] == {}, "ping answers empty result")

    tools = s.call("tools/list")["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    check(names == ["config_check", "draft_render", "email_send", "session_context",
                    "templates_list", "work_start", "work_status"],
          f"tools/list exposes all 7 tools ({', '.join(names)})")
    check(all("inputSchema" in t and "description" in t for t in tools),
          "every tool has a schema and description")
    check("error" in s.call("tools/call", {"name": "nope", "arguments": {}}),
          "unknown tool -> JSON-RPC error")
    check(s.call("does/not/exist").get("error", {}).get("code") == -32601,
          "unknown method -> -32601")

    print("\n== session transcript (the /gmailsum evidence source) ==")
    sc, err = s.tool("session_context", {"cwd": str(repo)})
    check(sc["ok"] and not err, "session_context reads the transcript", json.dumps(sc)[:300])
    texts = [p["text"] for p in sc["user_prompts"]]
    check(len(texts) == 2, f"duplicate + harness-marker prompts filtered (got {len(texts)})",
          json.dumps(texts)[:300])
    check(any("partial words" in t for t in texts), "real prompt kept verbatim")
    check(not any("subagent internal" in t for t in texts), "sidechain chatter excluded")
    check(not any("system-reminder" in t for t in texts), "system-reminder stripped from prompts")
    check(any(f.endswith("search.py") for f in sc["files_created"]), "Write recorded as created")
    check(any(f.endswith("ui.py") for f in sc["files_edited"]), "Edit recorded as edited")
    check(sc["errors_count"] == 1 and sc["errors"][0]["tool"] == "Bash",
          "failed tool call captured and attributed to the right tool")
    check(sc["commands_count"] == 1, "Bash command recorded")
    check(sc["branch"] == "main" and sc["duration_minutes"] is not None,
          "branch and duration derived")
    _, err = s.tool("session_context", {"cwd": str(tmp / "nowhere")})
    check(err, "missing transcript reports cleanly instead of crashing")

    print("\n== work tracking ==")
    st, _ = s.tool("work_status", {"repo_path": str(repo)})
    check(not st["ok"], "work_status without a baseline explains itself")
    ws, _ = s.tool("work_start", {"repo_path": str(repo), "client": "acme",
                                  "request": "Search misses partial words.", "title": "search"})
    check(ws["ok"] and len(ws["baseline"]) == 40, "work_start pins a 40-char baseline SHA")
    (repo / "search.py").write_text("def search(q):\n    return fuzzy(q)\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "fix: fuzzy"], cwd=repo, capture_output=True)
    (repo / "search.py").write_text("def search(q):\n    return fuzzy(q, 150)\n")
    (repo / "NOTES.md").write_text("scratch\n")
    st, _ = s.tool("work_status", {"repo_path": str(repo)})
    check(len(st["changes"]["commits"]) == 1, "commit since baseline reported")
    check(any(f["path"] == "search.py" for f in st["changes"]["files_changed"]),
          "UNCOMMITTED working-tree change included in evidence")
    check("NOTES.md" in st["changes"]["untracked"], "untracked file reported")

    print("\n== drafting without recipients (content first) ==")
    drafts_dir = Path(st["drafts_dir"])
    drafts_dir.mkdir(parents=True, exist_ok=True)
    draft = drafts_dir / "2026-08-03-acme-search.md"
    write_draft(draft)
    r, err = s.tool("draft_render", {"draft_path": str(draft)})
    check(r["ok"] and not err, "draft with empty to: still renders", json.dumps(r)[:300])
    check(r["needs_recipients"] is True, "render flags that recipients are missing")
    check("Invoice" in r["text_preview"], "body content present in text part")
    check("**" not in r["text_preview"], "markdown markers stripped from text part")
    _, err = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": r["hash"],
                                   "confirm_recipient": ""})
    check(err, "sending a draft with no recipients is refused")

    print("\n== recipients ==")
    write_draft(draft, to="jane@acme.com, ops@acme.com", cc="boss@acme.com",
                bcc="archive@acme.com")
    r, err = s.tool("draft_render", {"draft_path": str(draft)})
    check(not err and r["to"] == ["jane@acme.com", "ops@acme.com"],
          f"multiple To addresses parsed: {r.get('to')}")
    check(r["cc"] == ["boss@acme.com"] and r["bcc"] == ["archive@acme.com"], "cc and bcc parsed")
    check(r["needs_recipients"] is False, "render no longer flags missing recipients")
    good_hash = r["hash"]

    _, err = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": good_hash,
                                   "confirm_recipient": "jane@acme.com"})
    check(err, "confirming only some of the To addresses is refused")
    dry, err = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": good_hash,
                                     "confirm_recipient": "ops@acme.com, jane@acme.com",
                                     "dry_run": True})
    check(not err and dry["sent"] is False,
          "recipient confirmation is order-independent", json.dumps(dry)[:200])
    check(dry["payload_preview"].get("from") == "Karan Garg <karan@example.com>",
          f"From header built as name + address: {dry['payload_preview'].get('from')!r}")

    for label, kw in [
        ("malformed address", {"to": "not-an-email"}),
        ("display-name form", {"to": "Jane <jane@acme.com>"}),
        ("same address in to and cc", {"to": "jane@acme.com", "cc": "jane@acme.com"}),
        ("one bad address among good", {"to": "jane@acme.com, broken@"}),
    ]:
        p = drafts_dir / f"bad-{label.replace(' ', '-')}.md"
        write_draft(p, **kw)
        _, e = s.tool("draft_render", {"draft_path": str(p)})
        check(e, f"rejected: {label}")

    print("\n== attachments ==")
    write_draft(draft, to="jane@acme.com",
                attachments=f"{assets / 'before.png'}, {assets / 'after.png'}")
    r, err = s.tool("draft_render", {"draft_path": str(draft)})
    check(not err and len(r["attachments"]) == 2, "two attachments resolved",
          json.dumps(r.get("attachments"))[:300])
    check(all(a["mimeType"] == "image/png" for a in r["attachments"]),
          "png mime type detected")
    check(all("data" not in a for a in r["attachments"]),
          "render does not echo base64 blobs back into the model's context")
    att_hash = r["hash"]

    for label, value in [
        ("disallowed extension", str(assets / "danger.exe")),
        ("missing file", str(assets / "nope.png")),
        ("empty file", str(assets / "empty.png")),
        ("relative path that does not resolve", "somewhere/else.png"),
    ]:
        p = drafts_dir / f"att-{label.replace(' ', '-')}.md"
        write_draft(p, to="jane@acme.com", attachments=value)
        _, e = s.tool("draft_render", {"draft_path": str(p)})
        check(e, f"attachment rejected: {label}")

    print("\n== safety gates ==")
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": "deadbeef",
                                 "confirm_recipient": "jane@acme.com"})
    check(e, "stale hash is refused")
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": att_hash,
                                 "confirm_recipient": "someone@else.com"})
    check(e, "recipient mismatch is refused")

    outside = drafts_dir / "outside.md"
    write_draft(outside, to="stranger@evil.com")
    o, _ = s.tool("draft_render", {"draft_path": str(outside)})
    _, e = s.tool("email_send", {"draft_path": str(outside), "confirm_hash": o["hash"],
                                 "confirm_recipient": "stranger@evil.com"})
    check(e, "recipient outside allowed_recipients is refused")

    cfg_file, original = home / "config.json", (home / "config.json").read_text()
    cfg_file.write_text(original.replace('"paused": false', '"paused": true'))
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": att_hash,
                                 "confirm_recipient": "jane@acme.com"})
    check(e, "paused: true blocks sending")

    cfg_file.write_text(original.replace('"from_email": "karan@example.com",', ''))
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": att_hash,
                                 "confirm_recipient": "jane@acme.com"})
    check(e, "missing from_email blocks sending (SMTP needs a real sender)")
    cfg_file.write_text(original)

    print("\n== real send with attachments (to the mock) ==")
    sent, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": att_hash,
                                    "confirm_recipient": "jane@acme.com"})
    check(not e and sent.get("sent") is True, "send succeeds end to end", json.dumps(sent)[:400])
    check(sent.get("n8n_status") == 200, "webhook returned 200")
    check(sorted(sent.get("attachments", [])) == ["after.png", "before.png"],
          "both attachments reported as sent")

    landed = sorted(preview.glob("*.png"))
    check(len(landed) == 2, f"webhook decoded {len(landed)} attachment(s) to disk")
    check(all(p.read_bytes() == TINY_PNG for p in landed),
          "attachment bytes survive base64 round trip exactly")

    html_files = list(preview.glob("*.html"))
    check(bool(html_files), "rendered HTML received")
    if html_files:
        body = html_files[0].read_text()
        for label, needle in [("bold inline-styled", "<strong style="),
                              ("bullets styled", "<li style="),
                              ("link rendered", '<a href="https://example.com/search"'),
                              ("italic rendered", "<em>")]:
            check(needle in body, label)
        check("{{" not in body, "no unfilled {{ placeholders }} leaked into the email")
    log = home / "sent.log"
    check(log.exists() and "jane@acme.com" in log.read_text(), "send appended to sent.log")

    cfg_file.write_text(original.replace(secret, "wrong-secret"))
    _, e = s.tool("email_send", {"draft_path": str(draft), "confirm_hash": att_hash,
                                 "confirm_recipient": "jane@acme.com"})
    check(e, "wrong webhook_secret surfaces as an error, not a silent success")
    cfg_file.write_text(original)

    print("\n== malformed drafts ==")
    for label, body in [
        ("no frontmatter", "Hi Jane,\n"),
        ("unclosed frontmatter", "---\nto: a@b.com\nsubject: x\n"),
        ("missing subject", "---\nto: a@b.com\n---\n\nbody\n"),
        ("empty body", "---\nto: a@b.com\nsubject: x\n---\n\n"),
        ("unknown field", "---\nto: a@b.com\nsubject: x\nfrom: me\n---\n\nbody\n"),
        ("bad template name", "---\nto: a@b.com\nsubject: x\ntemplate: nope\n---\n\nbody\n"),
    ]:
        p = drafts_dir / f"bad-{label.replace(' ', '-')}.md"
        p.write_text(body, encoding="utf-8")
        out, e = s.tool("draft_render", {"draft_path": str(p)})
        check(e and "error" in out, f"rejected with a clear message: {label}",
              json.dumps(out)[:200])
    _, e = s.tool("draft_render", {"draft_path": str(drafts_dir / "nothere.md")})
    check(e, "missing draft file reports cleanly")

    print("\n== config ==")
    cfg, e = s.tool("config_check", {})
    check(cfg["ok"] and not e, "config_check passes on a valid config")
    check(sorted(cfg["templates"]) == ["client-update", "plain"],
          f"both templates found: {cfg['templates']}")
    cfg_file.write_text(json.dumps({"webhook_url": "https://YOURNAME.app.n8n.cloud/webhook/clientmail-send",
                                    "webhook_secret": "x", "from_name": "Your Name"}))
    cfg, _ = s.tool("config_check", {})
    check(not cfg["ok"] and any("example value" in p for p in cfg["problems"]),
          "a fresh unconfigured install is reported as NOT usable")
    cfg_file.write_text(original)

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
