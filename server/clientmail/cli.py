"""Command-line entry point -- setup, diagnosis, and sending outside Claude Code.

The MCP server is the normal path. This exists so that when something is wrong you
can find out why without a model in the loop.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import attachments, drafts, render, send, session, store
from .server import tool_config_check

PKG_ROOT = Path(__file__).resolve().parent.parent.parent


def _print_header(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m")


def cmd_init(args) -> int:
    store.ensure_dirs()
    cfg = store.config_path()
    if cfg.exists() and not args.force:
        print(f"config already exists: {cfg}\nUse --force to overwrite it.")
        return 1
    example = PKG_ROOT / "config.example.json"
    if not example.exists():
        print(f"Cannot find {example}", file=sys.stderr)
        return 1
    shutil.copy(example, cfg)

    src_templates = PKG_ROOT / "templates"
    if src_templates.exists():
        for t in src_templates.iterdir():
            if t.is_file():
                shutil.copy(t, store.templates_dir() / t.name)

    import secrets
    suggestion = secrets.token_urlsafe(32)
    print(f"created {cfg}")
    print(f"templates -> {store.templates_dir()}")
    print(f"\nSuggested webhook_secret (use the same value in n8n's Validate node):\n  {suggestion}")
    print(f"\nNext: edit {cfg}, then run  clientmail check")
    return 0


def cmd_check(args) -> int:
    result = tool_config_check({"ping": args.ping})
    _print_header("paths")
    for key in ("home", "config_file", "drafts_dir", "templates_dir"):
        print(f"  {key:14} {result[key]}")
    print(f"  {'templates':14} {', '.join(result['templates']) or '(none)'}")
    print(f"  {'clients':14} {', '.join(result['clients']) or '(none)'}")

    if result["problems"]:
        _print_header("problems")
        for p in result["problems"]:
            print(f"  \033[31mx\033[0m {p}")
    if result["warnings"]:
        _print_header("warnings")
        for w in result["warnings"]:
            print(f"  \033[33m!\033[0m {w}")
    if "ping" in result:
        _print_header("webhook")
        ping = result["ping"]
        if ping.get("reachable"):
            print(f"  \033[32mreachable\033[0m  HTTP {ping.get('status')}  {ping.get('body', '')[:200]}")
        else:
            print(f"  \033[31munreachable\033[0m  {ping.get('error')}")
            return 1
    if not result["problems"]:
        _print_header("ok")
        print("  config is usable.")
    return 0 if result["ok"] else 1


def cmd_templates(args) -> int:
    store.ensure_dirs()
    items = render.describe_templates()
    if not items:
        print(f"No templates in {store.templates_dir()} -- re-run the installer.")
        return 1
    _print_header(f"templates in {store.templates_dir()}")
    for t in items:
        text_part = "html+text" if t["has_text_part"] else "html only"
        print(f"  \033[1m{t['name']}\033[0m  ({text_part})")
        if t["description"]:
            print(f"    {t['description']}")
    return 0


def cmd_session(args) -> int:
    """Exactly what /gmailsum sees. Use this when a summary comes out wrong --
    it shows whether the evidence was bad or the writing was."""
    d = session.digest(args.cwd, session_id=args.session_id)
    _print_header("session")
    print(f"  id        {d['session_id']}")
    print(f"  transcript{'':1}{d['transcript']}")
    print(f"  cwd       {d['cwd']}  (branch {d['branch']})")
    print(f"  span      {d['duration_minutes']} min, {d['record_count']} records")

    _print_header(f"what the user asked ({len(d['user_prompts'])} prompts)")
    for p in d["user_prompts"]:
        first = p["text"].strip().split("\n")[0]
        print(f"  - {first[:100]}{'...' if len(first) > 100 else ''}")
    if d["user_prompts_dropped"]:
        print(f"  ({d['user_prompts_dropped']} older prompts dropped for length)")

    _print_header("files")
    print(f"  created   {d['files_created_count']}")
    for f in d["files_created"][:12]:
        print(f"            {f}")
    print(f"  edited    {d['files_edited_count']}")
    for f in d["files_edited"][:12]:
        print(f"            {f}")

    _print_header("activity")
    print(f"  commands  {d['commands_count']}")
    print(f"  errors    {d['errors_count']}")
    for e in d["errors"][:5]:
        print(f"            {e['tool']}: {e['excerpt'][:80]}")
    print(f"  tools     {', '.join(f'{k}x{v}' for k, v in d['tool_counts'].items())}")
    return 0


def cmd_render(args) -> int:
    cfg = store.load_config()
    parsed = drafts.read(args.draft)
    rendered = render.render_draft(parsed, cfg)

    _print_header("headers")
    print(f"  to       {', '.join(parsed['meta']['to']) or '(not set yet)'}")
    if parsed["meta"].get("cc"):
        print(f"  cc       {', '.join(parsed['meta']['cc'])}")
    if parsed["meta"].get("bcc"):
        print(f"  bcc      {', '.join(parsed['meta']['bcc'])}")
    print(f"  subject  {parsed['meta']['subject']}")
    print(f"  template {rendered['template']}")
    print(f"  hash     {parsed['hash']}")
    _print_header("plain text")
    print(rendered["text"])

    out = Path(args.out) if args.out else store.home() / "preview.html"
    out.write_text(rendered["html"], encoding="utf-8")
    _print_header("html preview")
    print(f"  {out}\n  open it with:  open {out}")
    return 0


def cmd_send(args) -> int:
    cfg = store.load_config()
    parsed = drafts.read(args.draft)
    drafts.require_recipients(parsed["meta"])
    rendered = render.render_draft(parsed, cfg)
    send.preflight(cfg)
    send.check_recipients(parsed["meta"], cfg)
    files = attachments.collect(
        parsed["meta"].get("attachments", []), draft_dir=Path(parsed["dir"]),
        max_total_mb=cfg.get("max_attachment_mb", attachments.DEFAULT_MAX_TOTAL_MB))

    print(rendered["text"])
    print("-" * 55)
    print(f"to:      {', '.join(parsed['meta']['to'])}")
    if parsed["meta"].get("cc"):
        print(f"cc:      {', '.join(parsed['meta']['cc'])}")
    if parsed["meta"].get("bcc"):
        print(f"bcc:     {', '.join(parsed['meta']['bcc'])}")
    print(f"subject: {parsed['meta']['subject']}")
    for f in attachments.summarise(files):
        print(f"attach:  {f['fileName']}  ({f['kb']} KB)")
    if not args.yes:
        answer = input("\nSend this? type 'send' to confirm: ").strip().lower()
        if answer != "send":
            print("cancelled.")
            return 1

    payload = send.build_payload(parsed, rendered, cfg, attachments=files)
    result = send.post(payload, cfg)
    store.append_sent_log({
        "to": payload["to"], "subject": payload["subject"], "draft": parsed["path"],
        "hash": parsed["hash"], "client": payload["client"], "status": result["status"],
        "via": "cli",
    })
    print(f"\nsent. n8n replied HTTP {result['status']}: {result['body'][:300]}")
    return 0


def cmd_test_email(args) -> int:
    """Full-chain test: real draft, real template, real Gmail, to yourself."""
    cfg = store.load_config()
    store.ensure_dirs()
    path = store.drafts_dir() / f"{time.strftime('%Y-%m-%d')}-selftest.md"
    path.write_text(
        f"---\nto: {args.address}\nsubject: clientmail test — please ignore\n"
        f"template: {args.template}\n---\n\n"
        "Hi there,\n\n"
        "This is a test of the clientmail pipeline. If you can read this, the whole chain "
        "works: Claude Code, the webhook, n8n, and Gmail.\n\n"
        "## Formatting check\n\n"
        "- **Bold** text and *italic* text\n"
        "- A [link](https://n8n.io) that should be clickable\n"
        "- A second bullet so spacing is visible\n\n"
        "Check this on your phone as well as your laptop.\n",
        encoding="utf-8")

    parsed = drafts.read(path)
    rendered = render.render_draft(parsed, cfg)
    send.preflight(cfg)
    payload = send.build_payload(parsed, rendered, cfg)
    print(f"sending test to {args.address} using template '{args.template}'...")
    result = send.post(payload, cfg)
    store.append_sent_log({
        "to": payload["to"], "subject": payload["subject"], "draft": str(path),
        "hash": parsed["hash"], "client": "", "status": result["status"],
        "via": "cli-test",
    })
    print(f"n8n replied HTTP {result['status']}: {result['body'][:300]}")
    print(f"\ndraft kept at {path}")
    print("Now go look at the actual email. A render is not proof it survives Gmail.")
    return 0


def cmd_doctor(args) -> int:
    """Everything a support question would need answered, in one place."""
    _print_header("versions")
    print(f"  python   {sys.version.split()[0]}  ({sys.executable})")
    try:
        git_v = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        git_v = "NOT FOUND -- work_start/work_status will fail"
    print(f"  git      {git_v}")

    _print_header("install")
    print(f"  package  {PKG_ROOT}")
    print(f"  home     {store.home()}")
    for p in (store.config_path(), store.templates_dir(), store.drafts_dir()):
        print(f"  {'exists ' if p.exists() else 'MISSING'}  {p}")

    skills = Path.home() / ".claude" / "skills"
    _print_header("claude code skills")
    for name in ("gmailsum", "client-work"):
        target = skills / name / "SKILL.md"
        print(f"  {'ok     ' if target.exists() else 'MISSING'}  {target}")

    _print_header("recent sends")
    log = store.sent_log()
    if not log.exists():
        print("  none yet")
    else:
        lines = log.read_text(encoding="utf-8").strip().split("\n")[-5:]
        for line in lines:
            try:
                e = json.loads(line)
                print(f"  {e.get('at', '?')}  {e.get('to', '?'):32} {e.get('subject', '')[:40]}")
            except json.JSONDecodeError:
                pass
    return cmd_check(argparse.Namespace(ping=args.ping))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="clientmail", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create config.json and install templates")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="validate config; --ping also tests the webhook")
    p.add_argument("--ping", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("doctor", help="everything needed to diagnose a broken install")
    p.add_argument("--ping", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("templates", help="list installed templates")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("session", help="show what /gmailsum would read from the session")
    p.add_argument("--cwd", default=".", help="directory of the session (default: here)")
    p.add_argument("--session-id", default=None)
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("render", help="render a draft; writes an HTML preview")
    p.add_argument("draft")
    p.add_argument("--out", help="where to write the HTML preview")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("send", help="send a draft (asks for confirmation)")
    p.add_argument("draft")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("test-email", help="send a real formatting test to yourself")
    p.add_argument("address")
    p.add_argument("--template", default="client-update")
    p.set_defaults(func=cmd_test_email)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (store.ConfigError, drafts.DraftError, render.TemplateError,
            send.SendBlocked, send.SendFailed, session.SessionError,
            attachments.AttachmentError) as exc:
        print(f"\n\033[31merror\033[0m {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
