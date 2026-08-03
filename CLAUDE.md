# clientmail — repo facts

Claude Code drafts and sends short non-technical client update emails, built from the git
diff rather than from conversation memory. Gmail is reached through an n8n webhook.

## Layout

```
server/clientmail/     store, drafts, render, gitinfo, send, server (MCP), cli
server/clientmail_server.py   MCP stdio entry point (registered with Claude Code)
server/clientmail_cli.py      `clientmail` command entry point
skills/                       copied to ~/.claude/skills/ by the installer
templates/                    stock templates, copied to ~/.clientmail/templates/
n8n/                          importable workflow + setup guide
tools/selftest.py             end-to-end test
tools/mock_webhook.py         stands in for n8n
```

Runtime state lives in `~/.clientmail/` (override with `CLIENTMAIL_HOME`). Code is
installed to `~/.clientmail/app/`, so **editing this repo does not change the installed
tool** — re-run `./install.sh` after changes.

## Non-negotiables

- **Zero third-party dependencies.** Stdlib only, Python 3.9+. This is what lets
  `install.sh` skip pip entirely, which is the main reason installs don't fail on other
  people's machines. Do not add a dependency without a very good reason.
- **stdout is JSON-RPC only.** Anything printed to stdout from `server/` corrupts the MCP
  stream and the server silently stops working. Diagnostics go to stderr via `_log`.
- **The safety gates are code, not prompt.** Hash check, recipient check, `paused`, and
  `allowed_recipients` live in `send.py` / `server.py` on purpose — a skill can be argued
  out of a rule, a Python `raise` cannot. Don't move them into the SKILL.md.
- **`work_status` must keep reporting the working tree**, not just commits. Claude often
  finishes without committing; a commit-only view drops that work from the client's email.

## Testing

```bash
python3 tools/selftest.py
```

Drives the real MCP server over a real pipe and posts over real HTTP to a local mock. Uses
a throwaway `CLIENTMAIL_HOME`, so it never touches your config or sends mail. Covers the
protocol, git evidence, rendering, every refusal path, and malformed drafts.

To test the installer without touching your real setup:

```bash
env HOME=/tmp/fakehome bash ./install.sh
```

## Known-unverified

The n8n workflow JSON (`n8n/clientmail-send.workflow.json`) has **not** been imported into
a live n8n instance. Node `typeVersion` values and the If node's condition structure are
believed correct for current n8n but may need adjusting on import. `n8n/SETUP.md` carries a
manual six-node build table as the fallback. Everything upstream of n8n is verified against
`tools/mock_webhook.py`.

## Gotchas

- `install.bat` must keep CRLF line endings — `.gitattributes` enforces this. LF batch
  files fail on Windows in confusing ways.
- The n8n Gmail node appends "sent automatically with n8n" unless
  `options.appendAttribution` is false. The shipped workflow sets it; verify it survives
  import before mailing a client.
- n8n's **Test** webhook URL only fires once per "Listen for test event" click. A 404 on
  send is almost always the Test URL, or an inactive workflow.
