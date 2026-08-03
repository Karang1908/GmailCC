# Setup

Start to finish, about 10 minutes. Five steps, and only step 3 needs any real attention.

- [1. Install](#1-install)
- [2. Get an n8n](#2-get-an-n8n)
- [3. Import the workflow](#3-import-the-workflow)
- [4. Connect the two ends](#4-connect-the-two-ends)
- [5. Prove it works](#5-prove-it-works)
- [If the import came out wrong](#if-the-import-came-out-wrong)

---

## 1. Install

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Karang1908/GmailCC/main/install.sh | bash
```

**Windows** — download the repo (green *Code* button → *Download ZIP*), unzip, double-click
`install.bat`.

You need **Python 3.9 or newer**. Nothing else — no `pip install`, no Node, no build step.

- macOS: `brew install python` if the installer says it can't find one
- Windows: python.org/downloads, and **tick "Add python.exe to PATH"** during install

The installer prints where everything went and generates a random `webhook_secret` for you.
Keep that terminal open — you need the secret in step 4.

> Re-running the installer is safe. It updates the code and leaves your `config.json`,
> your drafts, and any template you've edited untouched.

### If `clientmail` isn't found afterwards

The installer warns you when `~/.local/bin` isn't on your PATH. Fix it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

On Windows the shim is at `%USERPROFILE%\.clientmail\clientmail.cmd`.

---

## 2. Get an n8n

n8n is the piece that actually talks to Gmail. It exists in this design for exactly one
reason: **it owns the Google login so you don't have to build one.**

**n8n Cloud — recommended.** Sign up at [n8n.io](https://n8n.io). The free trial is enough
to set this up and test it. Its Gmail node uses n8n's own Google OAuth app, so connecting
your account is a "Sign in with Google" button. **No Google Cloud project, no OAuth consent
screen, no verification.**

**Self-hosted — free forever, more work:**

```bash
docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n
```

Then open http://localhost:5678. Be aware: self-hosted n8n has no n8n-owned Google app
behind it, so its Gmail node **will** ask you to create a Google Cloud project and supply
your own OAuth client ID and secret. If avoiding that was the point, use n8n Cloud.

> Self-hosting on localhost also means the webhook URL is `http://localhost:5678/...`,
> which only works while n8n is running on the same machine as Claude Code.

---

## 3. Import the workflow

In n8n: **Workflows → ⋯ menu → Import from File** → choose
`~/.clientmail/app/n8n/clientmail-send.workflow.json`.

You should see eight nodes:

```
Webhook → Validate → Is probe? ─┬─ true ──→ Respond Probe
                                │
                                └─ false ─→ Has attachments? ─┬─ true ─→ Send with attachments ─┐
                                                              │                                  ├→ Respond Sent
                                                              └─ false → Send plain ─────────────┘
```

**Why two Gmail nodes?** The Gmail node throws if its attachment field is empty, so an email
with no attachments would fail on a single node that always had attachments configured. The
branch keeps both cases working. You'll connect the same Gmail account to both.

### 3a. Set your secret

Open the **Validate** node. Near the top:

```js
const SECRET = 'REPLACE_WITH_YOUR_SECRET';
```

Replace it with the secret the installer generated. If you missed it:

```bash
python3 -c "import json,pathlib; print(json.loads((pathlib.Path.home()/'.clientmail/config.json').read_text())['webhook_secret'])"
```

Without a matching secret, anyone who discovers your webhook URL can send email from your
Gmail account.

### 3b. Connect Gmail — in **both** Gmail nodes

Open **Send with attachments** → *Credential to connect with* → **Create new** → sign in
with the Google account you want to send from → allow.

Then open **Send plain** and pick that **same credential** from the dropdown. You only
create it once.

In both nodes, check **Options → Append n8n Attribution is OFF**. Otherwise every email ends
with "This email was sent automatically with n8n", which is not what you want on a client
update. The imported workflow sets it off — confirm it survived the import.

### 3c. Activate

Toggle the workflow **Active** (top right). Nothing works until you do.

---

## 4. Connect the two ends

Open the **Webhook** node and copy the **Production URL**:

```
https://yourname.app.n8n.cloud/webhook/clientmail-send
```

> ⚠️ **Production URL, not Test URL.** The Test URL only fires once, after you click
> "Listen for test event". Using it is the single most common reason sending fails with a
> 404.

Now edit `~/.clientmail/config.json`:

```json
{
  "webhook_url": "https://yourname.app.n8n.cloud/webhook/clientmail-send",
  "webhook_secret": "the-same-secret-you-put-in-the-Validate-node",

  "from_name": "Your Name",
  "brand": {
    "name": "Your Studio",
    "color": "#2563eb",
    "signoff": "— Your Name",
    "site": "https://yoursite.com"
  },

  "paused": false,
  "allowed_recipients": ["your.own@email.com"],
  "clients": {}
}
```

**Leave `allowed_recipients` restricted to your own address for now.** It's a safety rail:
until you deliberately add a client, the tool physically cannot mail them. Set it to `[]`
to allow anyone once you're confident.

---

## 5. Prove it works

```bash
clientmail check --ping
```

Expect `reachable  HTTP 200`. This proves the URL is right, the workflow is active, and
your secret matches. It does **not** send anything.

Then send yourself a real one:

```bash
clientmail test-email you@yourdomain.com
```

Open it **on your phone as well as your laptop**. You're checking that:

- it isn't in spam
- the sender name is right
- bold, bullets and the link all render
- there's no n8n attribution line at the bottom

Then try the real thing — do some work in any repo and type:

```
/gmailsum
```

If `/gmailsum` isn't offered, restart Claude Code (skills are read at startup).

---

## If the import came out wrong

Node parameter formats change between n8n versions. If a node imports with empty or odd
settings, build these eight by hand:

| Node | Type | Settings |
|---|---|---|
| Webhook | Webhook | Method `POST`, Path `clientmail-send`, Respond `Using 'Respond to Webhook' node` |
| Validate | Code | Paste the JS from the `Validate` node in the JSON file |
| Is probe? | If | `{{ $json.probe }}` → Boolean → is true |
| Respond Probe | Respond to Webhook | Respond With `JSON`, body `{{ JSON.stringify({ ok: true, probe: true }) }}` |
| Has attachments? | If | `{{ $json.hasAttachments }}` → Boolean → is true |
| Send with attachments | Gmail | see below, **plus** Options → Attachments → Attachment Field Name `{{ $json.attachmentFields }}` |
| Send plain | Gmail | see below, no Attachments option |
| Respond Sent | Respond to Webhook | Respond With `JSON`, body `{{ JSON.stringify({ ok: true, messageId: $json.id \|\| '' }) }}` |

Both Gmail nodes: Resource `Message`, Operation `Send`, To `{{ $json.to }}`, Subject
`{{ $json.subject }}`, Email Type `HTML`, Message `{{ $json.html }}`; Options → CC
`{{ $json.cc }}`, BCC `{{ $json.bcc }}`, Send Replies To `{{ $json.replyTo }}`, Sender Name
`{{ $json.fromName }}`, **Append n8n Attribution off**.

Wiring: `Is probe?` true → Respond Probe, false → `Has attachments?`. `Has attachments?`
true → Send with attachments, false → Send plain. Both Gmail nodes → Respond Sent.

---

## Troubleshooting

Run `clientmail doctor` first — it reports your Python, git, install paths, whether the
skills are in place, and your recent sends.

| Symptom | Cause |
|---|---|
| `404` from n8n | Workflow not Active, or you used the Test URL |
| `401` / "secret does not match" | `webhook_secret` ≠ `SECRET` in the Validate node |
| "Attachment not found" | Attachment Field Name must be `{{ $json.attachmentFields }}` |
| Send succeeds, no email | Gmail credential is on the wrong Google account — check n8n's Executions tab |
| "sent automatically with n8n" | Append Attribution still on in one of the Gmail nodes |
| Raw HTML in the email body | Email Type is `Text`, should be `HTML` |
| Recipient refused | `allowed_recipients` — add the address or `"@theirdomain.com"` |
| Sending refused entirely | `"paused": true` in config.json |
| `/gmailsum` missing | Re-run the installer, then restart Claude Code |
