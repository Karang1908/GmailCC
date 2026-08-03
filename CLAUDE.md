# clientmail — repo facts

`/gmailsum` in Claude Code turns a work session into a short non-technical email, gets it
approved, and sends it through Gmail via an n8n webhook. The summary is built from the
session transcript on disk plus the git diff — not from the model's memory of the chat.

## Layout

```
server/clientmail/
  session.py       reads ~/.claude/projects/<slug>/<id>.jsonl -> evidence
  gitinfo.py       commits + working-tree + untracked changes since a baseline
  drafts.py        frontmatter parsing, address validation, content hashing
  attachments.py   resolve / allowlist / size-cap / base64
  render.py        markdown -> email HTML, template filling
  send.py          payload build + guards + POST to n8n
  server.py        MCP stdio server (the 7 tools)
  cli.py           `clientmail` command
  store.py         paths, config, work sessions
server/clientmail_server.py   MCP entry point (registered with Claude Code)
server/clientmail_cli.py      CLI entry point
skills/gmailsum/              the main skill
skills/client-work/           optional git-baseline pinning
templates/                    stock templates -> ~/.clientmail/templates/
n8n/                          importable workflow
tools/selftest.py             71-check end-to-end suite
tools/mock_webhook.py         stands in for n8n, decodes attachments to disk
```

Runtime state lives in `~/.clientmail/` (override with `CLIENTMAIL_HOME`). Code is
installed to `~/.clientmail/app/`, so **editing this repo does not change the installed
tool** — re-run `./install.sh` after changes. This has bitten before: a fix appeared to do
nothing because the installed copy was stale.

## Non-negotiables

- **Zero third-party dependencies.** Stdlib only, Python 3.9+. This is what lets
  `install.sh` skip pip entirely, which is the main reason installs don't fail on other
  people's machines. Do not add a dependency without a very good reason.
- **stdout is JSON-RPC only.** Anything printed to stdout from `server/` corrupts the MCP
  stream and the server silently stops working. Diagnostics go to stderr via `_log`.
- **The safety gates are code, not prompt.** Hash check, recipient check, `paused`,
  `allowed_recipients`, address validation — all `raise` in Python on purpose. A skill can
  be argued out of a rule; a `raise` cannot. Don't move them into SKILL.md.
- **`work_status` must keep reporting the working tree**, not just commits. Claude often
  finishes without committing, and a commit-only view drops that work from the email.
- **One skill matching "email the client".** `/client-update` was folded into `/gmailsum`;
  two skills with overlapping descriptions made Claude pick between them at random. The
  installer actively deletes a stale `~/.claude/skills/client-update`.

## Session transcript parsing

Format quirks that are load-bearing in `session.py` — all verified against real transcripts:

- The project dir slug is the absolute cwd with **every non-alphanumeric char** replaced by
  `-` (`/Users/x/.claude/jobs` → `-Users-x--claude-jobs`, note the double dash).
- **Each user prompt appears several times** (session bridging) and the copies are not
  always byte-identical, so dedupe keys on the first 200 chars of whitespace-normalised
  text and keeps the longest copy.
- `[Request interrupted by user]` and friends are harness markers, not user speech.
- `<system-reminder>` blocks are injected into user turns and must be stripped — they must
  never end up quoted in a client's email.
- `isSidechain: true` records are subagent chatter; excluded.
- A live session's last line may be partially flushed; a JSON decode error there is normal.

## n8n specifics

The workflow uses **`n8n-nodes-base.emailSend` (SMTP)**, not the Gmail node. That is a
deliberate choice for self-hosted n8n, verified against Google's docs: the Gmail node needs
Google OAuth, which on self-hosted means your own Google Cloud project, and because Gmail
scopes are "sensitive" an app in Testing status gets refresh tokens that **expire after 7
days**. An app password over SMTP has none of that.

Verified against `EmailSend/v2/send.operation.ts`:

- Top-level params: `fromEmail`, `toEmail`, `subject`, `emailFormat` (`text`/`html`/`both`),
  `text`, `html`.
- Inside `options`: `ccEmail`, `bccEmail`, `replyTo`, `attachments` (inline, `cid:` refs),
  `fileAttachments` (regular attachments), `appendAttribution`, `allowUnauthorizedCerts`.
- **The attachment option key is version-dependent, and getting it wrong fails silently.**
  On the released node (verified by reading `send.operation.js` inside a live n8n 2.8.4
  install) it is `options.attachments` — a comma-separated string of binary property names,
  consumed via `if (options.attachments && item.binary)`. n8n's `master` branch has since
  renamed this to `fileAttachments` and repurposed `attachments` for inline `cid:` images.
  **Read the node source in the target install, not the docs or master.** n8n silently
  drops option keys it does not recognise on import, so a wrong key produces a workflow
  that looks fine and never attaches anything.
- The guard means an empty string is safe, which is why one send node covers mail with and
  without attachments.
  (The Gmail node is the opposite: `assertBinaryData` **throws** on an empty name, which
  forced a two-node branch when we used it. Don't reintroduce that pattern here.)
- `appendAttribution: false` matters — otherwise every client email ends with "This email
  was sent automatically with n8n".
- Credential type is `smtp`. For Gmail: `smtp.gmail.com`, port 465, SSL on, user = the
  address, password = the 16-char app password.
- `from_email` in config **must** equal the authenticated account; Gmail rewrites or
  rejects a mismatched From.

## Testing

```bash
python3 tools/selftest.py                 # 71 checks
env HOME=/tmp/fakehome bash ./install.sh  # installer, without touching your real setup
```

The suite drives the real MCP server over a real pipe, posts over real HTTP to a local
mock, and asserts attachment bytes survive the base64 round trip exactly. It uses a
throwaway `CLIENTMAIL_HOME` **and** a throwaway `HOME` (for synthetic transcripts), so it
never touches your config and never sends mail.

## Known-unverified

The n8n workflow JSON has **not** been imported into a live n8n instance. Node
`typeVersion` values and the If node condition structure are believed correct but unproven;
the parameter names above *are* verified against source. `SETUP.md` carries a manual
eight-node build table as the fallback. Everything upstream of n8n is tested.

## Gotchas

- `install.bat` must keep CRLF endings — `.gitattributes` enforces it, but a direct edit
  can normalise it back to LF. Check with `file install.bat`.
- `install.sh` is run via `curl | bash`, where `BASH_SOURCE[0]` is **unset** and `set -u`
  makes reading it fatal. Guard any use of it.
- `raw.githubusercontent.com` caches for a few minutes; a just-pushed installer fix may
  appear missing. Verify against the API or a fresh clone, not the CDN.
- n8n's **Test** webhook URL fires once per "Listen for test event" click. A 404 on send is
  almost always that, or an inactive workflow.
