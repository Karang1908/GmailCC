# clientmail

Finish client work in Claude Code, get a short non-technical update email drafted from the
**actual git diff**, revise it in chat or in your editor, send it through Gmail.

```
/client-work        →  pins the baseline + the client's request, verbatim
   ...you build the thing...
/client-update      →  reads the diff, writes the email, shows it to you
   "make it warmer"  →  rewrites, shows again
   "send"            →  goes out via n8n → Gmail
```

The email says what the client could not do before and can do now. Nothing else.

---

## What makes this different from asking Claude to "write an email about what we did"

| | |
|---|---|
| **Built from evidence, not memory** | `/client-work` pins a git SHA. The summary is generated from `git diff` against it — including uncommitted and untracked changes. Claude cannot claim work that isn't in the diff. |
| **Old state → new state, enforced** | Every bullet is `**Thing they recognise** — was X, now Y`. "Search now supports partial words" is a fail; "searching *invo* returned nothing, now it finds *Invoice*" is a pass. |
| **Jargon is mechanically banned** | The skill carries a substitution table — endpoint, cache, refactor, race condition, null, deployed, repo — with what to write instead. |
| **You approve exact bytes** | `email_send` requires the content hash of the draft you were shown. If the file changed after you approved it — by you or by Claude — the send is refused by the server, not by a prompt. |
| **Wrong-client protection** | Sending also requires restating the recipient, so the To: address is visible in the approval prompt rather than buried in a file path. |
| **A refusal you can't talk it out of** | `paused`, `allowed_recipients`, and the hash check live in Python. A model cannot be persuaded past them. |

---

## Install

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Karang1908/GmailCC/main/install.sh | bash
```

**Windows** — download the repo, double-click `install.bat`.

**From a clone** — `./install.sh` (it detects the local checkout and installs from it).

The installer needs Python 3.9+ and nothing else. It never runs `pip`; the whole tool is
stdlib. It's safe to re-run — your `config.json`, drafts and edited templates are left alone.

It puts:

```
~/.clientmail/app/          the code
~/.clientmail/config.json   your settings (generated secret included)
~/.clientmail/templates/    email templates, yours to edit
~/.clientmail/drafts/       every draft, kept
~/.clientmail/sent.log      what went out, when, to whom
~/.claude/skills/           /client-work and /client-update
~/.local/bin/clientmail     CLI for setup and troubleshooting
```

and registers the `clientmail` MCP server with Claude Code at user scope, so the skills
work in **any** repo.

### Then: n8n, once

See [`n8n/SETUP.md`](n8n/SETUP.md). Import one workflow, paste your secret, click "Sign in
with Google", copy the webhook URL into `config.json`.

On **n8n Cloud** this needs no Google Cloud project at all — that is the entire reason n8n
is in this design. Self-hosted n8n will ask you for your own Google OAuth credentials.

### Prove it before you point it at a client

```bash
clientmail check --ping                       # config valid, n8n reachable, secret matches
clientmail test-email you@yourdomain.com      # a real email, through the real chain
```

Open that test on your phone as well as your laptop. `allowed_recipients` ships locked to a
single address on purpose, so the first send to a real client is refused until you
deliberately add them.

---

## Daily use

```
/client-work
```

Paste what the client asked for, verbatim. Claude pins the SHA and confirms.

Build the thing. Commit or don't — both are tracked.

```
/client-update
```

Claude reads the diff, writes the draft to `~/.clientmail/drafts/`, and shows you the plain
text. Then:

- `"send"` — it goes
- `"drop the last bullet"` / `"warmer"` / `"mention the invoice bug"` — rewritten, shown again
- open the `.md` file and edit it yourself — Claude re-reads it before sending
- `"use the plain template"` — swaps the look

Nothing sends until you say so, and what sends is byte-for-byte what you last saw.

## Templates

Two ship: `client-update` (branded card) and `plain` (looks like you typed it).

Ask Claude for a new one — *"make a template matching this email I got from our designer"*
— and it writes it into `~/.clientmail/templates/`. Placeholders:

`{{ subject }}` `{{ preheader }}` `{{ body_html }}` `{{ body_text }}` `{{ brand_name }}`
`{{ brand_color }}` `{{ brand_signoff }}` `{{ brand_site }}` `{{ year }}`

Templates must use inline styles and table layout — Gmail strips `<style>` on forward and
Outlook renders with Word. `client-update.html` is the reference to copy.

## Config

`~/.clientmail/config.json`

| Key | |
|---|---|
| `webhook_url` | n8n **Production** webhook URL (not the Test URL) |
| `webhook_secret` | must equal `SECRET` in n8n's Validate node |
| `from_name` | display name the client sees |
| `brand` | `name`, `color`, `signoff`, `site` — fills the templates |
| `paused` | `true` blocks all sending. Use while testing. |
| `allowed_recipients` | `[]` allows anyone; `["@acme.com"]` allows a domain; ships restricted |
| `clients` | short keys → `{name, email, template}` so you can say "acme" |

## CLI

```
clientmail check [--ping]        validate config, test the webhook
clientmail doctor                everything needed to diagnose a broken install
clientmail templates             list templates and where they live
clientmail render <draft.md>     print the text part, write an HTML preview
clientmail send <draft.md>       send with a typed confirmation
clientmail test-email <address>  full-chain formatting test
```

## MCP tools

`work_start` · `work_status` · `draft_render` · `email_send` · `templates_list` · `config_check`

**Do not add `email_send` to your auto-approved tools.** Its approval prompt is the last
human checkpoint before mail leaves your account.

## Development

```bash
python3 tools/selftest.py                    # 44 checks, real JSON-RPC, real HTTP
python3 tools/mock_webhook.py --port 8787 --secret test
```

`mock_webhook.py` stands in for n8n and writes the rendered HTML to disk, so the whole
pipeline is testable without an n8n instance and without sending mail.

## Limitations

- **Attachments are not supported.** Screenshots would suit before/after updates well; the
  payload has room for it, the n8n side does not. Next thing to build.
- **No threading.** Every update is a new email, not a reply on an existing thread.
- **Send-only.** It cannot read the client's original email out of your inbox; you paste it.
- **One work session per repo.** Starting a new one replaces the previous baseline.
