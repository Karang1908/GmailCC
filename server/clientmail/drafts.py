"""Draft files: frontmatter + markdown body, addressed by content hash.

The hash is the review contract. `draft_render` hands back the hash of exactly
what it rendered; `email_send` refuses to send unless the file still hashes to
the value the human approved. Any edit -- by the user in their editor, or by
Claude -- invalidates the approval and forces a fresh review.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

LIST_FIELDS = {"cc", "bcc"}
KNOWN_FIELDS = {
    "to", "cc", "bcc", "subject", "template", "client", "reply_to", "from_name",
}


class DraftError(Exception):
    pass


def content_hash(text: str) -> str:
    """Hash of the draft's exact bytes. Newline-normalised so an editor that
    rewrites line endings doesn't spuriously invalidate an approval."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise DraftError(
            "Draft is missing its frontmatter. The file must start with a line "
            "containing exactly '---', then 'key: value' lines, then '---'."
        )
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:]).strip("\n")
    raise DraftError("Draft frontmatter is never closed by a second '---' line.")


def parse(text: str) -> dict:
    fm_raw, body = _split_frontmatter(text)
    meta: dict = {}
    for lineno, line in enumerate(fm_raw.split("\n"), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise DraftError(f"Frontmatter line {lineno} is not 'key: value': {line!r}")
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key in LIST_FIELDS:
            meta[key] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            meta[key] = value

    unknown = set(meta) - KNOWN_FIELDS
    if unknown:
        raise DraftError(
            f"Unknown frontmatter field(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(KNOWN_FIELDS))}."
        )
    if not meta.get("to"):
        raise DraftError("Draft frontmatter needs a 'to:' address.")
    if not meta.get("subject"):
        raise DraftError("Draft frontmatter needs a 'subject:'.")
    if not body.strip():
        raise DraftError("Draft has no body text below the frontmatter.")

    meta.setdefault("cc", [])
    meta.setdefault("bcc", [])
    return {"meta": meta, "body": body}


def read(path: str | Path) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        raise DraftError(f"No draft at {p}")
    text = p.read_text(encoding="utf-8")
    parsed = parse(text)
    parsed["path"] = str(p)
    parsed["hash"] = content_hash(text)
    parsed["raw"] = text
    return parsed


def slugify(value: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug[:limit].rstrip("-")) or "update"
