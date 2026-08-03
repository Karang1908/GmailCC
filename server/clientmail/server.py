"""MCP stdio server for clientmail.

Hand-rolled JSON-RPC rather than the `mcp` SDK, on purpose: it makes the whole
tool a dependency-free stdlib program, so `install.sh` never has to run pip and
can never fail on somebody else's Python. The surface used here (initialize,
tools/list, tools/call, ping) has been stable across MCP revisions.

Hard rule: stdout carries JSON-RPC and nothing else. All diagnostics go to stderr.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from . import drafts, gitinfo, render, send, store

SERVER_NAME = "clientmail"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "work_start",
        "description": (
            "Pin the start of a piece of client work. Records the repo's current git SHA "
            "and the client's request verbatim, so the update email can later be built from "
            "the real diff instead of from memory. Call this BEFORE doing the work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path inside the repo the work happens in."},
                "client": {"type": "string", "description": "Client key from config.json, or a plain email address."},
                "request": {"type": "string", "description": "The client's ask, copied verbatim. Do not paraphrase."},
                "title": {"type": "string", "description": "Short internal label for this piece of work."},
            },
            "required": ["repo_path", "client", "request"],
        },
    },
    {
        "name": "work_status",
        "description": (
            "The evidence for the summary: commits since the pinned baseline, every file that "
            "differs from it right now (committed, staged AND unstaged), untracked files, and "
            "the original request. Use this as the ONLY source of truth for what changed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "draft_render",
        "description": (
            "Render a draft file through its template and return the exact HTML and plain text "
            "that would be sent, plus the draft's content hash. Show the plain text to the user "
            "for review. The returned hash is what email_send requires."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_path": {"type": "string", "description": "Path to the draft .md file."},
            },
            "required": ["draft_path"],
        },
    },
    {
        "name": "email_send",
        "description": (
            "Send a reviewed draft via the n8n webhook. Refuses unless the file still hashes to "
            "confirm_hash (i.e. nothing changed since the user approved it) and confirm_recipient "
            "matches the draft's To: address. Only call this after the user has explicitly said to send."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_path": {"type": "string"},
                "confirm_hash": {"type": "string", "description": "Hash from the draft_render the user reviewed."},
                "confirm_recipient": {"type": "string", "description": "The To: address, restated so it is visible in the approval prompt."},
                "dry_run": {"type": "boolean", "description": "Run every check and build the payload, but do not POST. Default false."},
            },
            "required": ["draft_path", "confirm_hash", "confirm_recipient"],
        },
    },
    {
        "name": "templates_list",
        "description": "List installed email templates and where they live, so they can be read or edited.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "config_check",
        "description": (
            "Validate config.json and report every path the tool uses (drafts dir, templates dir). "
            "Call this first in a session -- it tells you where to write the draft file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ping": {"type": "boolean", "description": "Also POST a harmless probe to the webhook to prove it is reachable."},
            },
        },
    },
]


# --- tool implementations ------------------------------------------------

def tool_work_start(args: dict) -> dict:
    repo = gitinfo.repo_root(args["repo_path"])
    cfg = store.load_config()
    client = store.resolve_client(cfg, args["client"])
    session = {
        "repo": repo,
        "baseline": gitinfo.head_sha(repo),
        "branch": gitinfo.current_branch(repo),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "client": client,
        "request": args["request"],
        "title": args.get("title", ""),
        "clean_at_start": gitinfo.is_clean(repo),
    }
    path = store.save_session(repo, session)
    return {
        "ok": True,
        "session_file": str(path),
        **session,
        "note": (
            "Baseline pinned. Uncommitted changes already present at start are NOT "
            "distinguishable from your work later -- commit or stash first if that matters."
        ) if not session["clean_at_start"] else "Baseline pinned on a clean tree.",
        "drafts_dir": str(store.drafts_dir()),
    }


def tool_work_status(args: dict) -> dict:
    repo = gitinfo.repo_root(args["repo_path"])
    session = store.load_session(repo)
    if not session:
        return {
            "ok": False,
            "error": (
                f"No work session pinned for {repo}. Run work_start first, or if the work is "
                f"already done, ask the user which git ref the work started from."
            ),
        }
    changes = gitinfo.changes_since(repo, session["baseline"])
    return {
        "ok": True,
        "request": session["request"],
        "client": session["client"],
        "title": session.get("title", ""),
        "started_at": session["started_at"],
        "changes": changes,
        "drafts_dir": str(store.drafts_dir()),
    }


def tool_draft_render(args: dict) -> dict:
    cfg = store.load_config()
    parsed = drafts.read(args["draft_path"])
    rendered = render.render_draft(parsed, cfg)
    return {
        "ok": True,
        "path": parsed["path"],
        "hash": parsed["hash"],
        "to": parsed["meta"]["to"],
        "cc": parsed["meta"].get("cc", []),
        "subject": parsed["meta"]["subject"],
        "template": rendered["template"],
        "text_preview": rendered["text"],
        "html_bytes": len(rendered["html"]),
        "next": (
            "Show text_preview to the user. To send, call email_send with "
            f"confirm_hash={parsed['hash']!r} and confirm_recipient={parsed['meta']['to']!r}."
        ),
    }


def tool_email_send(args: dict) -> dict:
    cfg = store.load_config()
    parsed = drafts.read(args["draft_path"])
    dry_run = bool(args.get("dry_run", False))

    if parsed["hash"] != args["confirm_hash"]:
        raise send.SendBlocked(
            f"The draft changed after it was reviewed. Approved hash was "
            f"{args['confirm_hash']}, file is now {parsed['hash']}. Re-run draft_render, show "
            f"the user what it says now, and only send once they approve the new version."
        )
    if parsed["meta"]["to"].strip().lower() != args["confirm_recipient"].strip().lower():
        raise send.SendBlocked(
            f"confirm_recipient {args['confirm_recipient']!r} does not match the draft's "
            f"To: {parsed['meta']['to']!r}. Refusing in case the wrong draft was passed."
        )

    send.preflight(cfg)
    send.check_recipients(parsed["meta"], cfg)
    rendered = render.render_draft(parsed, cfg)
    payload = send.build_payload(parsed, rendered, cfg)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "sent": False,
            "would_post_to": cfg["webhook_url"],
            "payload_preview": {k: v for k, v in payload.items() if k not in ("html", "text")},
            "html_bytes": len(payload["html"]),
        }

    result = send.post(payload, cfg)
    store.append_sent_log({
        "to": payload["to"], "cc": payload["cc"], "subject": payload["subject"],
        "draft": parsed["path"], "hash": parsed["hash"],
        "client": payload["client"], "status": result["status"],
    })
    return {
        "ok": True,
        "sent": True,
        "to": payload["to"],
        "subject": payload["subject"],
        "n8n_status": result["status"],
        "n8n_response": result["body"],
        "logged_to": str(store.sent_log()),
    }


def tool_templates_list(args: dict) -> dict:
    store.ensure_dirs()
    return {"ok": True, "templates_dir": str(store.templates_dir()),
            "templates": render.describe_templates()}


def tool_config_check(args: dict) -> dict:
    store.ensure_dirs()
    problems, warnings = [], []
    cfg = None
    try:
        cfg = store.load_config()
    except store.ConfigError as exc:
        problems.append(str(exc))

    if cfg:
        if not cfg.get("webhook_url"):
            problems.append("webhook_url is empty -- nothing can be sent until it is set.")
        if not cfg.get("webhook_secret"):
            warnings.append("webhook_secret is empty: anyone who learns your webhook URL could send mail as you.")

        # A fresh install copies config.example.json, whose values are all non-empty.
        # Without this, first run reports "config is usable" when nothing is set up yet.
        brand = cfg.get("brand") or {}
        if "YOURNAME.app.n8n.cloud" in str(cfg.get("webhook_url", "")):
            problems.append(
                "webhook_url is still the example value. Paste your n8n Production webhook "
                "URL -- see n8n/SETUP.md.")
        if "paste-the-same-secret" in str(cfg.get("webhook_secret", "")):
            problems.append(
                "webhook_secret is still the example value. Generate one and put the same "
                "string in n8n's Validate node.")
        if str(cfg.get("from_name", "")).strip() == "Your Name":
            warnings.append("from_name is still 'Your Name' -- the client will see that.")
        if str(brand.get("name", "")).strip() == "Your Studio":
            warnings.append("brand.name is still 'Your Studio'.")
        if str(brand.get("signoff", "")).strip() == "— Your Name":
            warnings.append("brand.signoff is still the example sign-off.")
        if "yoursite.com" in str(brand.get("site", "")):
            warnings.append("brand.site is still the example URL.")
        if "your.own@email.com" in (cfg.get("allowed_recipients") or []):
            warnings.append(
                "allowed_recipients still contains the example address 'your.own@email.com' "
                "-- replace it with your own before testing.")
        if "acme" in (cfg.get("clients") or {}):
            warnings.append(
                "The example client 'acme' is still configured. Replace it with a real client.")
        if not cfg.get("from_name"):
            warnings.append("from_name is empty -- the client will see the raw Gmail account name.")
        if not (cfg.get("brand") or {}).get("signoff"):
            warnings.append("brand.signoff is empty -- emails will end without a sign-off line.")
        if cfg.get("paused"):
            warnings.append("paused is true: email_send will refuse until you set it to false.")
        if not cfg.get("clients"):
            warnings.append("No clients configured; you can still pass full email addresses.")
        if not cfg.get("allowed_recipients"):
            warnings.append("allowed_recipients is empty, so any address may be mailed. "
                            "While testing, set it to [\"your@email.com\"].")

    if not render.stock_template_names():
        problems.append(f"No templates found in {store.templates_dir()} -- re-run the installer.")

    result = {
        "ok": not problems,
        "home": str(store.home()),
        "config_file": str(store.config_path()),
        "drafts_dir": str(store.drafts_dir()),
        "templates_dir": str(store.templates_dir()),
        "templates": render.stock_template_names(),
        "clients": sorted((cfg or {}).get("clients", {})),
        "problems": problems,
        "warnings": warnings,
    }

    if args.get("ping") and cfg and cfg.get("webhook_url"):
        try:
            probe = send.post({"probe": True, "subject": "clientmail connectivity probe"}, cfg)
            result["ping"] = {"reachable": True, **probe}
        except send.SendFailed as exc:
            result["ping"] = {"reachable": False, "error": str(exc)}
    return result


HANDLERS = {
    "work_start": tool_work_start,
    "work_status": tool_work_status,
    "draft_render": tool_draft_render,
    "email_send": tool_email_send,
    "templates_list": tool_templates_list,
    "config_check": tool_config_check,
}

EXPECTED_ERRORS = (
    store.ConfigError, drafts.DraftError, render.TemplateError,
    gitinfo.GitError, send.SendBlocked, send.SendFailed,
)


# --- JSON-RPC plumbing ---------------------------------------------------

def _log(msg: str) -> None:
    print(f"[clientmail] {msg}", file=sys.stderr, flush=True)


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(req_id, payload: dict) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": payload})


def _error(req_id, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _text_result(req_id, data: dict, is_error: bool = False) -> None:
    _result(req_id, {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}],
        "isError": is_error,
    })


def handle(message: dict) -> None:
    method = message.get("method")
    req_id = message.get("id")

    if req_id is None:  # notification: never answer
        return

    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        _result(req_id, {
            "protocolVersion": requested or DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if method == "ping":
        _result(req_id, {})
        return

    if method == "tools/list":
        _result(req_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            _error(req_id, -32602, f"Unknown tool: {name}")
            return
        try:
            _text_result(req_id, handler(args))
        except EXPECTED_ERRORS as exc:
            # A refusal or misconfiguration is information for the model, not a
            # protocol fault -- hand it back as tool output so it can act on it.
            _text_result(req_id, {"ok": False, "error": str(exc),
                                  "error_type": type(exc).__name__}, is_error=True)
        except KeyError as exc:
            _text_result(req_id, {"ok": False,
                                  "error": f"Missing required argument: {exc}"}, is_error=True)
        except Exception as exc:  # noqa: BLE001 - last resort, must not kill the server
            _log("unhandled error:\n" + traceback.format_exc())
            _text_result(req_id, {"ok": False,
                                  "error": f"Unexpected {type(exc).__name__}: {exc}"}, is_error=True)
        return

    _error(req_id, -32601, f"Method not found: {method}")


def main() -> int:
    store.ensure_dirs()
    _log(f"v{SERVER_VERSION} ready (home={store.home()})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _log(f"ignoring non-JSON line: {line[:120]!r}")
            continue
        try:
            handle(message)
        except Exception:  # noqa: BLE001
            _log("fatal in handler:\n" + traceback.format_exc())
            if message.get("id") is not None:
                _error(message["id"], -32603, "Internal server error")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
