"""Reading, checking and encoding files to attach to an email.

Images go out as real attachments, not inline in the HTML. That is not a
shortcut -- Gmail strips `data:` URI images, so an inline-encoded screenshot
simply does not render for the recipient. A CID-referenced inline image would
need the message assembled as raw MIME, which the n8n Gmail node does not expose.
An attachment is the option that actually arrives.

The extension allowlist is deliberate: this tool sends mail to clients, and a
wrong path should fail loudly rather than quietly mail somebody an executable.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

ALLOWED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    ".pdf", ".txt", ".csv", ".md", ".log", ".json", ".zip",
}

FALLBACK_TYPES = {
    ".md": "text/markdown",
    ".log": "text/plain",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

DEFAULT_MAX_TOTAL_MB = 8


class AttachmentError(Exception):
    pass


def _resolve(raw: str, draft_dir: Path | None) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        raise AttachmentError(f"Attachment not found: {candidate}")

    tried = []
    for base in [p for p in (draft_dir, Path.cwd()) if p is not None]:
        guess = (base / candidate).resolve()
        tried.append(str(guess))
        if guess.exists():
            return guess
    raise AttachmentError(
        f"Attachment {raw!r} not found. Tried: {', '.join(tried)}. "
        f"Use an absolute path -- the server's working directory is not yours."
    )


def _mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or FALLBACK_TYPES.get(path.suffix.lower(), "application/octet-stream")


def collect(paths: list[str], draft_dir: Path | None = None,
            max_total_mb: float = DEFAULT_MAX_TOTAL_MB) -> list[dict]:
    """Resolve, validate and base64-encode each attachment."""
    out: list[dict] = []
    total = 0
    seen_names: dict[str, int] = {}

    for raw in paths:
        raw = str(raw).strip()
        if not raw:
            continue
        path = _resolve(raw, draft_dir)

        if not path.is_file():
            raise AttachmentError(f"Not a file: {path}")
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise AttachmentError(
                f"Refusing to attach {path.name}: '{suffix or 'no extension'}' is not an "
                f"allowed type. Allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}."
            )

        data = path.read_bytes()
        if not data:
            raise AttachmentError(f"{path.name} is empty (0 bytes).")
        total += len(data)

        # Two files called screenshot.png would collide in the recipient's client.
        name = path.name
        if name in seen_names:
            seen_names[name] += 1
            name = f"{path.stem}-{seen_names[name]}{path.suffix}"
        else:
            seen_names[name] = 1

        out.append({
            "fileName": name,
            "mimeType": _mime_for(path),
            "size": len(data),
            "source": str(path),
            "data": base64.b64encode(data).decode("ascii"),
        })

    limit = max_total_mb * 1024 * 1024
    if total > limit:
        raise AttachmentError(
            f"Attachments total {total / 1048576:.1f} MB, over the {max_total_mb} MB limit. "
            f"Base64 encoding adds about a third on top of that, and n8n rejects oversized "
            f"webhook bodies. Shrink the images or send fewer."
        )
    return out


def summarise(items: list[dict]) -> list[dict]:
    """Attachment list without the base64 payload, safe to show or log."""
    return [
        {"fileName": i["fileName"], "mimeType": i["mimeType"],
         "kb": round(i["size"] / 1024, 1), "source": i["source"]}
        for i in items
    ]
