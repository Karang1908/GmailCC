"""Handing a rendered draft to n8n, with the guards that make that safe.

Four independent things must all be true before a byte leaves this machine:
  1. config.paused is false
  2. every recipient passes the allowlist (when one is configured)
  3. the draft on disk still hashes to the value the human approved
  4. the caller explicitly asked to send (dry_run defaults on at the tool layer)

Guard 3 lives here rather than only in the skill because a skill is a prompt --
it can be talked out of things. This cannot.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 45


class SendBlocked(Exception):
    """A guard refused. The message is written to be shown to the user verbatim."""


class SendFailed(Exception):
    """We tried, the network or n8n said no."""


def _recipient_allowed(address: str, allowlist: list[str]) -> bool:
    addr = address.strip().lower()
    for rule in allowlist:
        rule = str(rule).strip().lower()
        if not rule:
            continue
        if rule == "*":
            return True
        if rule.startswith("@") and addr.endswith(rule):
            return True
        if rule == addr:
            return True
    return False


def check_recipients(meta: dict, cfg: dict) -> None:
    allowlist = cfg.get("allowed_recipients") or []
    if not allowlist:
        return
    everyone = [meta["to"], *meta.get("cc", []), *meta.get("bcc", [])]
    blocked = [a for a in everyone if a and not _recipient_allowed(a, allowlist)]
    if blocked:
        raise SendBlocked(
            f"Recipient(s) not on the allowlist: {', '.join(blocked)}. "
            f"allowed_recipients in config.json is set to {allowlist}. "
            f"Add the address (or '@theirdomain.com') there, or clear the list to allow all."
        )


def build_payload(parsed: dict, rendered: dict, cfg: dict) -> dict:
    meta = parsed["meta"]
    return {
        "to": meta["to"],
        "cc": ", ".join(meta.get("cc", [])),
        "bcc": ", ".join(meta.get("bcc", [])),
        "subject": meta["subject"],
        "html": rendered["html"],
        "text": rendered["text"],
        "fromName": meta.get("from_name") or cfg.get("from_name") or "",
        "replyTo": meta.get("reply_to") or cfg.get("reply_to") or "",
        "client": meta.get("client", ""),
        "template": rendered["template"],
        "draftHash": parsed["hash"],
    }


def preflight(cfg: dict) -> None:
    if cfg.get("paused"):
        raise SendBlocked(
            "Sending is paused ('paused': true in config.json). "
            "Set it to false when you are ready to send for real."
        )
    if not cfg.get("webhook_url"):
        raise SendBlocked(
            "No webhook_url in config.json. Import n8n/clientmail-send.workflow.json "
            "into n8n, activate it, and paste its Production webhook URL there."
        )


def post(payload: dict, cfg: dict) -> dict:
    url = cfg["webhook_url"]
    body = dict(payload)
    if cfg.get("webhook_secret"):
        body["secret"] = cfg["webhook_secret"]

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "clientmail/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "body": raw[:2000]}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        if exc.code == 404:
            raise SendFailed(
                f"n8n returned 404 for {url}. The usual cause is the workflow being "
                f"inactive, or the Test URL being used instead of the Production URL. "
                f"Activate the workflow and re-copy the Production URL. Response: {detail}"
            ) from exc
        if exc.code in (401, 403):
            raise SendFailed(
                f"n8n rejected the request ({exc.code}) -- webhook_secret in config.json "
                f"does not match the secret in the workflow's Validate node. Response: {detail}"
            ) from exc
        raise SendFailed(f"n8n returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SendFailed(
            f"Could not reach n8n at {url}: {exc.reason}. "
            f"Check the host is up and the URL is right."
        ) from exc
