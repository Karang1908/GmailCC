"""Paths, config and work-session state.

Everything the tool remembers lives under CLIENTMAIL_HOME (default ~/.clientmail).
Code and stock templates are installed there too, so an update is just a re-run of
the installer: it never touches config.json, drafts/ or sessions/.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

DEFAULT_CONFIG = {
    "webhook_url": "",
    "webhook_secret": "",
    "from_name": "",
    "reply_to": "",
    "default_template": "client-update",
    "brand": {
        "name": "",
        "color": "#2563eb",
        "signoff": "",
        "site": "",
    },
    "paused": False,
    "allowed_recipients": [],
    "clients": {},
}


def home() -> Path:
    return Path(os.environ.get("CLIENTMAIL_HOME") or (Path.home() / ".clientmail"))


def config_path() -> Path:
    return home() / "config.json"


def drafts_dir() -> Path:
    return home() / "drafts"


def sessions_dir() -> Path:
    return home() / "sessions"


def templates_dir() -> Path:
    return home() / "templates"


def sent_log() -> Path:
    return home() / "sent.log"


def ensure_dirs() -> None:
    for d in (home(), drafts_dir(), sessions_dir(), templates_dir()):
        d.mkdir(parents=True, exist_ok=True)


class ConfigError(Exception):
    pass


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        raise ConfigError(
            f"No config at {path}. Copy config.example.json there and fill in "
            f"webhook_url + webhook_secret from your n8n workflow."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg.update({k: v for k, v in raw.items() if k != "brand"})
    if isinstance(raw.get("brand"), dict):
        cfg["brand"].update(raw["brand"])
    return cfg


def resolve_client(cfg: dict, key: str) -> dict:
    """Look a client up by config key, then by name, then by literal email."""
    clients = cfg.get("clients") or {}
    if key in clients:
        return {"key": key, **clients[key]}
    lowered = key.strip().lower()
    for ckey, entry in clients.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "")).strip().lower() == lowered:
            return {"key": ckey, **entry}
        if str(entry.get("email", "")).strip().lower() == lowered:
            return {"key": ckey, **entry}
    if "@" in key:
        return {"key": key, "name": key.split("@")[0], "email": key}
    raise ConfigError(
        f"Unknown client {key!r}. Known: {', '.join(sorted(clients)) or '(none configured)'}. "
        f"Add it to clients in config.json, or pass a full email address."
    )


# --- work sessions -------------------------------------------------------

def _session_key(repo_path: str) -> str:
    resolved = str(Path(repo_path).expanduser().resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def session_path(repo_path: str) -> Path:
    return sessions_dir() / f"{_session_key(repo_path)}.json"


def save_session(repo_path: str, data: dict) -> Path:
    ensure_dirs()
    path = session_path(repo_path)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_session(repo_path: str) -> dict | None:
    path = session_path(repo_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def append_sent_log(entry: dict) -> None:
    ensure_dirs()
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}
    with sent_log().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
