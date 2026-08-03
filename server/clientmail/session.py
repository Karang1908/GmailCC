"""Read the Claude Code session transcript and distil it into evidence.

Claude already has the session in its context window, so why read the file?
Because context is lossy and memory is not evidence. Long sessions get compacted,
early detail drops out, and "what I remember doing" is exactly the failure mode
this tool exists to avoid. The transcript is the record of what actually
happened: every prompt the human typed, every file written, every command run,
every error returned.

Transcripts live at ~/.claude/projects/<slug>/<session-id>.jsonl, where <slug> is
the working directory with every non-alphanumeric character replaced by '-'.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

WRITE_TOOLS = {"Write", "NotebookEdit"}
EDIT_TOOLS = {"Edit", "MultiEdit"}

# Machinery that the harness injects into user turns. It is not something the
# human said, and it must never end up quoted in a client's email.
NOISE_PATTERNS = [
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S),
    re.compile(r"<command-name>.*?</command-name>", re.S),
    re.compile(r"<command-message>.*?</command-message>", re.S),
    re.compile(r"<command-args>.*?</command-args>", re.S),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S),
    re.compile(r"^Caveat: The messages below.*?$", re.M),
    re.compile(r"^\w+ hook success:.*?$", re.M),
    re.compile(r"^\[method\].*?$", re.M),
]


class SessionError(Exception):
    pass


def project_slug(cwd: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "-", str(Path(cwd).expanduser().resolve()))


def projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def find_transcript(cwd: str, session_id: str | None = None) -> Path:
    """Locate the transcript. An explicit session id is exact; otherwise take the
    most recently modified transcript for this directory, which is the live
    session in every case except two Claude Code windows open on one repo."""
    d = projects_root() / project_slug(cwd)
    if not d.is_dir():
        raise SessionError(
            f"No transcript directory for {cwd} (looked in {d}). "
            f"Either this directory has no Claude Code history, or the session is brand new."
        )
    if session_id:
        exact = d / f"{session_id}.jsonl"
        if not exact.exists():
            raise SessionError(f"No transcript {exact}")
        return exact
    candidates = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SessionError(f"No .jsonl transcripts in {d}")
    return candidates[0]


# Things the harness writes into the user turn that are not the user speaking.
HARNESS_MARKERS = re.compile(
    r"^\[(request interrupted|tool use was rejected|no response requested)"
    r"[^\]]*\]$|^\(no content\)$",
    re.I,
)


def _is_harness_marker(text: str) -> bool:
    return bool(HARNESS_MARKERS.match(text.strip()))


def _clean(text: str) -> str:
    for pattern in NOISE_PATTERNS:
        text = pattern.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _is_tool_result(content) -> bool:
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _minutes_between(start: str | None, end: str | None) -> float | None:
    if not (start and end):
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return round((b - a).total_seconds() / 60, 1)
    except ValueError:
        return None


def digest(cwd: str, session_id: str | None = None,
           max_prompt_chars: int = 9000, max_commands: int = 40,
           include_assistant: bool = True) -> dict:
    path = find_transcript(cwd, session_id)

    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a partially flushed final line is normal on a live session

    prompts: list[dict] = []
    seen_prompts: dict[str, dict] = {}
    assistant_notes: list[str] = []
    created: list[str] = []
    edited: list[str] = []
    commands: list[dict] = []
    errors: list[dict] = []
    tool_counts: dict[str, int] = {}
    tool_names: dict[str, str] = {}
    first_ts = last_ts = None
    meta_cwd = meta_branch = meta_session = None

    for rec in records:
        if rec.get("isSidechain"):
            continue  # subagent chatter, not this conversation

        ts = rec.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        meta_cwd = meta_cwd or rec.get("cwd")
        meta_branch = meta_branch or rec.get("gitBranch")
        meta_session = meta_session or rec.get("sessionId")

        rtype = rec.get("type")
        message = rec.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")

        if rtype == "user":
            if _is_tool_result(content):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    if block.get("is_error"):
                        body = block.get("content")
                        excerpt = body if isinstance(body, str) else _text_of(body)
                        errors.append({
                            "tool": tool_names.get(block.get("tool_use_id", ""), "?"),
                            "excerpt": _clean(excerpt)[:400],
                        })
                continue
            text = _clean(_text_of(content))
            if not text or _is_harness_marker(text):
                continue
            # The transcript records each prompt several times (session bridging),
            # and the copies are not always byte-identical. Keying on the opening
            # of the whitespace-normalised text collapses them; two genuinely
            # different prompts effectively never share their first 200 chars.
            key = re.sub(r"\s+", " ", text)[:200]
            if key in seen_prompts:
                prior = seen_prompts[key]
                if len(text) > len(prior["text"]):
                    prior["text"] = text  # keep the fullest copy
                continue
            entry = {"at": ts, "text": text}
            seen_prompts[key] = entry
            prompts.append(entry)

        elif rtype == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and include_assistant:
                    t = _clean(block.get("text", ""))
                    if t:
                        assistant_notes.append(t)
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                    if block.get("id"):
                        tool_names[block["id"]] = name
                    args = block.get("input") or {}
                    if not isinstance(args, dict):
                        continue
                    if name in WRITE_TOOLS and args.get("file_path"):
                        created.append(args["file_path"])
                    elif name in EDIT_TOOLS and args.get("file_path"):
                        edited.append(args["file_path"])
                    elif name == "Bash" and args.get("command"):
                        commands.append({
                            "description": args.get("description", ""),
                            "command": str(args["command"])[:300],
                        })

    # Keep prompts verbatim but bounded: the earliest ones state the goal, the
    # latest ones state the correction, and both matter more than the middle.
    prompt_budget, kept, dropped = max_prompt_chars, [], 0
    for p in prompts:
        if prompt_budget - len(p["text"]) < 0:
            dropped += 1
            continue
        prompt_budget -= len(p["text"])
        kept.append(p)

    def dedupe(seq: list[str]) -> list[str]:
        out, seen = [], set()
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    created_u, edited_u = dedupe(created), dedupe(edited)
    edited_u = [f for f in edited_u if f not in set(created_u)]

    return {
        "transcript": str(path),
        "session_id": meta_session or path.stem,
        "cwd": meta_cwd or str(Path(cwd).resolve()),
        "branch": meta_branch,
        "started_at": first_ts,
        "ended_at": last_ts,
        "duration_minutes": _minutes_between(first_ts, last_ts),
        "user_prompts": kept,
        "user_prompts_dropped": dropped,
        "assistant_notes": [n[:400] for n in assistant_notes[-6:]],
        "files_created": created_u[:100],
        "files_edited": edited_u[:100],
        "files_created_count": len(created_u),
        "files_edited_count": len(edited_u),
        "commands": commands[-max_commands:],
        "commands_count": len(commands),
        "errors": errors[:10],
        "errors_count": len(errors),
        "tool_counts": dict(sorted(tool_counts.items(), key=lambda kv: -kv[1])),
        "record_count": len(records),
    }
