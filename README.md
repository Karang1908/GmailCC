<h1>clientmail</h1>

**Finish work in Claude Code, type `/gmailsum`, and get a short non-technical email
drafted from what actually happened in the session — review it, say who it goes to, send it.**

Gmail is reached through n8n, so there is no Google Cloud project to create and no OAuth
consent screen to fight.

```
you: /gmailsum
     (optionally: extra context typed in, or screenshots attached)

Claude: reads the session transcript + the git diff
        writes a short plain-English email
        shows it to you

you: "make the second bullet warmer"
Claude: rewrites, shows again

you: "looks good"
Claude: Who should this go to? To / CC / BCC — commas for several people.

you: jane@acme.com, ops@acme.com
Claude: shows final headers → you say send → it's gone
```

---

## Contents

- [What problem this actually solves](#what-problem-this-actually-solves)
- [How it works](#how-it-works)
- [Install](#install)
- [Using it](#using-it)
- [Images and attachments](#images-and-attachments)
- [Templates](#templates)
- [Configuration](#configuration)
- [The CLI](#the-cli)
- [MCP tools](#mcp-tools)
- [Safety model](#safety-model)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

---

## What problem this actually solves

Asking Claude "write an email about what we did today" produces a confident, fluent, and
partly invented summary. It over-claims, because a model summarising its own conversation
from a context window that has been compacted is working from memory, not from evidence.
Then you paste it into Gmail and hope.

clientmail changes three things:

|  | |
|---|---|
| **Evidence, not memory** | The summary is built from the session transcript on disk — every prompt you typed, every file written, every command run, every error returned — plus the git diff. Not from what's still in the context window. |
| **Old state → new state** | Every bullet is `**Thing they recognise** — was X, now Y`. "Search now supports partial words" is a fail. "Searching *invo* returned nothing; it now finds *Invoice*" is a pass. |
| **You approve exact bytes** | Sending requires the content hash of the draft you were shown. If the file changed after you approved it — by you or by Claude — the send is refused in Python, not by a prompt. |

Plus a jargon table the skill must obey (`endpoint`, `cache`, `refactor`, `race condition`,
`null`, `deployed`, `repo` → what to write instead) and hard length limits, because "short"
is otherwise a matter of opinion.

## How it works

Three layers, each doing the one thing it's good at:

```
┌─ Claude Code ────────────────────────────────────────────────┐
│                                                              │
│   /gmailsum  ──►  SKILL.md          judgment                 │
│                   what to say, what to leave out,            │
│                   how to phrase it for a non-engineer        │
│                        │                                     │
│                        ▼                                     │
│                   MCP server        rules                    │
│                   (stdlib Python)   evidence gathering,      │
│                                     validation, refusals     │
└────────────────────────┬─────────────────────────────────────┘
                         │  HTTPS POST (JSON + shared secret)
                         ▼
                 ┌─ n8n workflow ──────────┐     delivery
                 │  Webhook → Validate      │    holds the Gmail
                 │    → probe? → respond    │    credential so you
                 │    → attachments? ──┐    │    never do OAuth
                 │        Gmail send ◄──┘   │
                 └──────────┬──────────────┘
                            ▼
                          Gmail
```

**Why the split matters.** A skill is a prompt — it can be reasoned with, and under enough
pressure a model will talk itself past a rule written in English. So the rules that must
never bend live in Python: the hash check, the recipient check, `paused`, and
`allowed_recipients` are `raise` statements in the MCP server. The skill handles taste;
the server handles consequences.

### Where the summary comes from

`session_context` reads `~/.claude/projects/<slug>/<session-id>.jsonl` — the transcript
Claude Code writes as you work — and distils it:

- **every prompt you typed**, verbatim and deduplicated (the format records each one
  several times, and mixes in harness markers like `[Request interrupted by user]`)
- **files created and edited**, from the actual tool calls
- **commands run**, and **errors returned**
- how long the session ran, on which branch

Sub-agent chatter and injected `<system-reminder>` blocks are stripped — they are not
things you said, and they must never end up quoted to a client.

If a baseline was pinned with `/client-work`, the git diff since then is added as a
cross-check. Git is secondary here: the session is the source.

### What gets sent

The draft is a file on disk — frontmatter plus markdown — so you can edit it in your own
editor, and it's diffable:

```markdown
---
to: jane@acme.com, ops@acme.com
cc: boss@acme.com
bcc:
subject: Search fixes are live
template: client-update
attachments: /Users/you/shots/before.png, /Users/you/shots/after.png
---

Hi Jane,

The search problems you flagged are fixed and live.

## What's different

- **Partial words** — searching "invo" used to return nothing; it now finds *Invoice* as you type.
- **Typing lag** — the page froze while you typed; it keeps up now.

Nothing needed from you.
```

The server renders that markdown into HTML through a template, produces a matching
plain-text part, base64-encodes any attachments, and POSTs the lot to n8n.

## Install

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Karang1908/GmailCC/main/install.sh | bash
```

**Windows** — download the repo and double-click `install.bat`.

**From a clone** — `./install.sh` detects the local checkout and installs from it.

Needs Python 3.9+ and nothing else. **The installer never runs `pip`** — the entire tool is
standard library, which is the single biggest reason installs don't fail on other people's
machines. Safe to re-run: your `config.json`, drafts and edited templates are left alone.

Then follow **[SETUP.md](SETUP.md)** for the one-time n8n step (~5 minutes).

### What lands where

```
~/.clientmail/app/          the code
~/.clientmail/config.json   your settings (a webhook secret is generated for you)
~/.clientmail/templates/    email templates, yours to edit
~/.clientmail/drafts/       every draft, kept
~/.clientmail/sent.log      what went out, when, to whom
~/.claude/skills/gmailsum/  the /gmailsum skill
~/.local/bin/clientmail     CLI for setup and troubleshooting
```

and the `clientmail` MCP server is registered with Claude Code at **user scope**, so
`/gmailsum` works in every repo, not just this one.

## Using it

Do your work as normal. Then:

```
/gmailsum
```

You can pass extra context in the same message, and it outranks anything Claude inferred:

```
/gmailsum this is for the Henderson launch — don't mention the database work
```

You can also **attach images to Claude** to explain something — a screenshot of the bug, a
design, the client's original message. Those sharpen the summary; they don't go in the
email unless you say so.

Then:

1. Claude shows you the draft. Say what to change, or edit the file directly — both work.
2. Once you approve the content, Claude asks for **To**, **CC** and **BCC** separately.
   Several people on a line: separate with commas. "none" or blank for CC/BCC.
3. Claude shows the final headers and asks once more.
4. You say send.

### Optional: pinning a baseline

For work spanning several sessions, `/client-work` records the git SHA and the client's
request verbatim at the start, so `/gmailsum` gets a precise diff later.

```
/client-work
```

Not required — `/gmailsum` works fine without it.

## Images and attachments

Put absolute paths in the draft's `attachments:` field, comma-separated. Allowed: `png`
`jpg` `jpeg` `gif` `webp` `svg` `bmp` `pdf` `txt` `csv` `md` `log` `json` `zip`. 8 MB total
by default (`max_attachment_mb` in config).

**Images are sent as attachments, not inline in the message body.** This is deliberate:
Gmail strips `data:` URI images, so an inline-encoded screenshot renders as *nothing* for
your recipient. A CID-referenced inline image would require assembling raw MIME, which the
n8n Gmail node doesn't expose. An attachment is the option that actually arrives.

The allowlist is deliberate too — a wrong path should fail loudly rather than quietly mail
somebody an executable.

## Templates

Two ship:

| | |
|---|---|
| `client-update` | Branded card with a coloured accent bar and sign-off block. Default. |
| `plain` | Looks like you typed it. No card, no colour, no footer. |

Ask Claude for another — *"make a template matching this email from our designer"* — and it
writes one into `~/.clientmail/templates/`.

Placeholders: `{{ subject }}` `{{ preheader }}` `{{ body_html }}` `{{ body_text }}` (txt
only) `{{ brand_name }}` `{{ brand_color }}` `{{ brand_signoff }}` `{{ brand_site }}`
`{{ year }}`. Unknown ones render empty rather than leaking `{{ brand_site }}` into an inbox.

Templates must use **inline styles** and **table layout** — Gmail strips `<style>` blocks on
forward and Outlook renders with Word. Copy `client-update.html` rather than starting fresh,
and send yourself a test: a render is not proof it survives a real mail client.

## Configuration

`~/.clientmail/config.json`

| Key | |
|---|---|
| `webhook_url` | n8n **Production** webhook URL (not the Test URL) |
| `webhook_secret` | must equal `SECRET` in n8n's Validate node |
| `from_name` | display name the recipient sees |
| `reply_to` | optional reply-to address |
| `default_template` | used when a draft doesn't name one |
| `brand` | `name`, `color`, `signoff`, `site` — fills the templates |
| `paused` | `true` blocks all sending. Use while testing. |
| `allowed_recipients` | `[]` allows anyone; `["@acme.com"]` allows a domain; ships restricted to one address |
| `max_attachment_mb` | total attachment budget, default 8 |
| `clients` | short keys → `{name, email, template}` |

## The CLI

```
clientmail check [--ping]        validate config; --ping tests the webhook end to end
clientmail doctor                everything needed to diagnose a broken install
clientmail session [--cwd .]     exactly what /gmailsum reads from the session
clientmail templates             list templates and where they live
clientmail render <draft.md>     print the text part, write an HTML preview
clientmail send <draft.md>       send with a typed confirmation
clientmail test-email <address>  full-chain formatting test
```

`clientmail session` is the one to reach for when a summary comes out wrong — it shows you
whether the *evidence* was bad or the *writing* was.

## MCP tools

| Tool | |
|---|---|
| `session_context` | distil the session transcript into evidence |
| `work_start` / `work_status` | pin and read a git baseline |
| `draft_render` | render a draft, return the text part + content hash |
| `email_send` | hash-gated, recipient-confirmed send |
| `templates_list` | list installed templates |
| `config_check` | validate config, optionally probe the webhook |

**Do not add `email_send` to your auto-approved tools.** Its approval prompt is the last
human checkpoint before mail leaves your account.

## Safety model

Five things must all be true before a byte leaves your machine:

1. `paused` is false
2. every recipient passes `allowed_recipients`
3. the draft still hashes to the value you approved
4. the draft has a `to:` address, and the caller restated it correctly
5. you approved the `email_send` tool call

Numbers 1–4 are Python. Number 5 is Claude Code's own permission prompt — which is why the
recipient is restated as an argument: it appears in that prompt, rather than being buried
in a file path you'd have to go and read.

Addresses are validated as real addresses. `Jane <jane@acme.com>` is rejected rather than
half-supported, because splitting that form on commas silently mangles any name containing
one. The same address in both To and CC is rejected — the person would get two copies.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `404` from n8n | Workflow not Active, or you used the Test URL instead of the Production URL |
| `401` / "secret does not match" | `webhook_secret` ≠ the `SECRET` in the Validate node |
| Send succeeds, no email arrives | Gmail credential connected to the wrong Google account — check n8n's Executions tab |
| "sent automatically with n8n" at the bottom | Append Attribution is still on in a Gmail node |
| Email arrives as raw HTML source | Email Type is `Text`, should be `HTML` |
| "Attachment not found" from n8n | The Gmail node's attachment field must be `{{ $json.attachmentFields }}` |
| Summary describes the wrong session | Two Claude Code windows on one repo; run `clientmail session` to see which transcript was read |
| `/gmailsum` not offered | Re-run the installer, then restart Claude Code |

Start with `clientmail doctor`.

## Limitations

- **No threading.** Every email is a new message, not a reply on an existing thread.
- **Send-only.** It can't read the client's original email out of your inbox; you paste or
  describe it.
- **No inline images**, for the reason described above — attachments only.
- **One work session per repo** for `/client-work`; starting a new one replaces the baseline.
- **n8n is required.** On n8n Cloud there's no Google Cloud project; self-hosted n8n will
  ask for your own Google OAuth credentials.

## Development

```bash
python3 tools/selftest.py       # 71 checks: real JSON-RPC, real HTTP, real image bytes
python3 tools/mock_webhook.py --port 8787 --secret test
```

`mock_webhook.py` stands in for n8n — same secret check, same response shape — and decodes
attachments back to disk, so the whole pipeline is testable without an n8n instance and
without sending mail.
