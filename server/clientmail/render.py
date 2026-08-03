"""Markdown -> email HTML, and template filling.

Deliberately a small subset of markdown: headings, bullets, bold, italic, links,
inline code, rules, paragraphs. That is everything a short client update needs,
and it keeps this file dependency-free and predictable.

Every generated tag carries an inline style. Gmail strips <style> blocks in
forwarded mail and Outlook's Word renderer ignores most of a stylesheet, so
inline is the only thing that reliably survives both.
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path

from . import store

P = 'style="margin:0 0 14px 0;font-size:15px;line-height:1.6;color:#1f2937;"'
H2 = 'style="margin:24px 0 10px 0;font-size:16px;line-height:1.4;color:#111827;font-weight:600;"'
H3 = 'style="margin:18px 0 8px 0;font-size:15px;line-height:1.4;color:#111827;font-weight:600;"'
UL = 'style="margin:0 0 14px 0;padding-left:20px;"'
LI = 'style="margin:0 0 7px 0;font-size:15px;line-height:1.6;color:#1f2937;"'
HR = 'style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;"'
CODE = ('style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        'font-size:13px;background:#f3f4f6;padding:1px 5px;border-radius:3px;"')


def _inline(text: str) -> str:
    """Escape, then apply inline markdown. Order matters: bold before italic so
    ``**x**`` is not eaten by the single-asterisk rule."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                 r'<a href="\2" style="color:{link};text-decoration:underline;">\1</a>', out)
    out = re.sub(r"`([^`]+)`", rf"<code {CODE}>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r'<strong style="font-weight:600;color:#111827;">\1</strong>', out)
    out = re.sub(r"(?<![\*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", out)
    return out


def markdown_to_html(md: str, link_color: str = "#2563eb") -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    parts: list[str] = []
    para: list[str] = []
    bullets: list[str] = []

    def flush_para() -> None:
        if para:
            parts.append(f"<p {P}>" + _inline(" ".join(para).strip()) + "</p>")
            para.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li {LI}>{_inline(b)}</li>" for b in bullets)
            parts.append(f"<ul {UL}>{items}</ul>")
            bullets.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_bullets()
        elif re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            flush_para()
            flush_bullets()
            parts.append(f"<hr {HR}>")
        elif stripped.startswith("### "):
            flush_para()
            flush_bullets()
            parts.append(f"<h3 {H3}>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            flush_para()
            flush_bullets()
            parts.append(f"<h2 {H2}>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            flush_para()
            flush_bullets()
            parts.append(f"<h2 {H2}>{_inline(stripped[2:])}</h2>")
        elif re.match(r"[-*+]\s+", stripped):
            flush_para()
            bullets.append(re.sub(r"^[-*+]\s+", "", stripped))
        else:
            flush_bullets()
            para.append(stripped)

    flush_para()
    flush_bullets()
    return "\n".join(parts).replace("{link}", link_color)


def markdown_to_text(md: str) -> str:
    """Plain-text alternative. Same content, markers softened rather than stripped
    entirely so structure is still readable in a text-only client."""
    out_lines = []
    for line in md.replace("\r\n", "\n").split("\n"):
        s = line.rstrip()
        s = re.sub(r"^#{1,6}\s+", "", s)
        s = re.sub(r"^([-*+])\s+", "  - ", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", s)
        s = s.replace("**", "").replace("`", "")
        s = re.sub(r"(?<![\*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?![\*\w])", r"\1", s)
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s.strip()):
            s = "-" * 40
        out_lines.append(s)
    return "\n".join(out_lines).strip() + "\n"


def preheader(md: str, limit: int = 110) -> str:
    """First real sentence, shown as the grey preview line next to the subject."""
    for line in md.split("\n"):
        s = line.strip()
        if s and not s.startswith(("#", "-", "*", ">")):
            flat = re.sub(r"[*`\[\]]|\(https?://[^)]*\)", "", s).strip()
            return flat[:limit]
    return ""


class TemplateError(Exception):
    pass


def load_template(name: str, ext: str) -> str:
    path = store.templates_dir() / f"{name}.{ext}"
    if not path.exists():
        available = sorted(p.stem for p in store.templates_dir().glob(f"*.{ext}"))
        raise TemplateError(
            f"No {ext} template named {name!r} in {store.templates_dir()}. "
            f"Available: {', '.join(available) or '(none)'}"
        )
    return path.read_text(encoding="utf-8")


def fill(template: str, values: dict) -> str:
    """Replace {{ key }} placeholders. Unknown placeholders collapse to empty
    string so a half-filled brand config renders clean rather than leaking
    '{{ brand_site }}' into a client's inbox."""
    def sub(match: re.Match) -> str:
        key = match.group(1).strip()
        return str(values.get(key, ""))
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", sub, template)


def render_draft(parsed: dict, cfg: dict) -> dict:
    """Turn a parsed draft + config into the exact html/text that would be sent."""
    meta, body = parsed["meta"], parsed["body"]
    brand = cfg.get("brand") or {}
    name = meta.get("template") or cfg.get("default_template") or "client-update"
    color = brand.get("color") or "#2563eb"

    values = {
        "subject": html.escape(meta["subject"], quote=False),
        "preheader": html.escape(preheader(body), quote=False),
        "body_html": markdown_to_html(body, link_color=color),
        "brand_name": html.escape(str(brand.get("name") or ""), quote=False),
        "brand_color": color,
        "brand_signoff": html.escape(str(brand.get("signoff") or ""), quote=False),
        "brand_site": str(brand.get("site") or ""),
        "year": time.strftime("%Y"),
    }
    html_out = fill(load_template(name, "html"), values)

    try:
        text_tpl = load_template(name, "txt")
        text_out = fill(text_tpl, {**values,
                                   "body_text": markdown_to_text(body),
                                   "brand_signoff": str(brand.get("signoff") or ""),
                                   "brand_name": str(brand.get("name") or "")})
    except TemplateError:
        text_out = markdown_to_text(body)

    return {"html": html_out, "text": text_out, "template": name}


def stock_template_names() -> list[str]:
    return sorted({p.stem for p in store.templates_dir().glob("*.html")})


def describe_templates() -> list[dict]:
    out = []
    for name in stock_template_names():
        p: Path = store.templates_dir() / f"{name}.html"
        first = ""
        for line in p.read_text(encoding="utf-8").split("\n"):
            if line.strip().startswith("<!--") and "desc:" in line:
                first = line.split("desc:", 1)[1].replace("-->", "").strip()
                break
        out.append({
            "name": name,
            "description": first,
            "has_text_part": (store.templates_dir() / f"{name}.txt").exists(),
            "path": str(p),
        })
    return out
