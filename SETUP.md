# Setup

Start to finish, about 15 minutes.

- [1. Install](#1-install)
- [2. Get an n8n](#2-get-an-n8n)
- [3. Get a Gmail app password](#3-get-a-gmail-app-password)
- [4. Import the workflow](#4-import-the-workflow)
- [5. Fill in the config](#5-fill-in-the-config)
- [6. Prove it works](#6-prove-it-works)
- [If the import came out wrong](#if-the-import-came-out-wrong)
- [Troubleshooting](#troubleshooting)

---

## 1. Install

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Karang1908/GmailCC/main/install.sh | bash
```

**Windows** — download the repo (green *Code* button → *Download ZIP*), unzip, double-click
`install.bat`.

You need **Python 3.9 or newer**. Nothing else — no `pip install`, no build step. (Verified
working on stock macOS Python 3.9.6 and on bash 3.2, so a Mac with no Homebrew is fine.)

The installer prints where everything went and generates a random `webhook_secret`. Keep
that terminal open — you need the secret in step 4.

> Re-running the installer is safe. It updates the code and leaves your `config.json`,
> your drafts, and any template you've edited untouched.

If `clientmail` isn't found afterwards:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

---

## 2. Get an n8n

n8n is the piece that actually sends the mail.

**Local (free, runs on your machine):**

```bash
npx n8n
```

Then open http://localhost:5678 and create the local owner account.

> ⚠️ `npx n8n` stops the moment you close that terminal window or reboot, and sending
> stops with it. For anything beyond testing, run it as a background service (`pm2`, a
> `launchd` plist on macOS, or Docker with `--restart unless-stopped`).

**n8n Cloud** works too — sign up at [n8n.io](https://n8n.io) and skip nothing else; the
rest of this guide is identical.

---

## 3. Get a Gmail app password

This is how n8n logs into Gmail. It takes about three minutes and needs no Google Cloud
project.

1. Go to **myaccount.google.com → Security**.
2. Turn on **2-Step Verification** if it isn't already. App passwords don't exist without it.
3. Search that page for **App passwords** (or go to
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)).
4. Name it anything — *clientmail* — and click **Create**.
5. Copy the **16-character code**. Spaces don't matter.

> **Why not the "Sign in with Google" button in n8n?** On self-hosted n8n that route
> requires you to create your own Google Cloud project, enable the Gmail API and configure
> a consent screen — and because Gmail's scopes count as sensitive, Google expires the
> connection **every 7 days** until the app passes review. An app password avoids all of it.

> **Can't find App passwords?** Some Google Workspace administrators disable them. If your
> account is a Workspace one and the option is missing, ask the admin to allow app
> passwords, or use n8n Cloud with the Gmail node instead.

---

## 4. Import the workflow

In n8n: **Workflows → ⋯ → Import from File** → choose
`~/.clientmail/app/n8n/clientmail-send.workflow.json`.

In the macOS file picker, press `Cmd+Shift+G` and paste that path if you can't see dotted
folders.

Six nodes:

```
Webhook → Validate → Is probe? ─┬─ true ──→ Respond Probe
                                └─ false ─→ Send Email → Respond Sent
```

### 4a. Set your secret

Open the **Validate** node and replace the placeholder, keeping the quotes:

```js
const SECRET = 'REPLACE_WITH_YOUR_SECRET';
```

Lost it? Print it back:

```bash
python3 -c "import json,pathlib; print(json.loads((pathlib.Path.home()/'.clientmail/config.json').read_text())['webhook_secret'])"
```

### 4b. Set up the SMTP credential

Open the **Send Email** node → *Credential to connect with* → **Create new**, and fill in:

| Field | Value |
|---|---|
| User | your full Gmail address |
| Password | the 16-character app password from step 3 |
| Host | `smtp.gmail.com` |
| Port | `465` |
| SSL/TLS | **on** |

Save. Check **Options → Append n8n Attribution is OFF** — otherwise every email ends with
"This email was sent automatically with n8n".

### 4c. Publish it

Top right of the editor:

- **n8n 2.x** — click **Publish**
- **n8n 1.x** — flip the **Active** toggle

Same thing under two names; the button was renamed in n8n 2.0. Nothing works until you
do it — an unpublished workflow returns `404 ... webhook is not registered` and n8n's own
error text tells you so.

### 4d. Copy the Production URL

Open the **Webhook** node and copy the **Production URL**. Running locally it looks like:

```
http://localhost:5678/webhook/clientmail-send
```

> ⚠️ **Production, not Test.** The Test URL only fires once, after you click "Listen for
> test event". Using it is the most common reason sending fails with a 404.

---

## 5. Fill in the config

```bash
open -e ~/.clientmail/config.json
```

```json
{
  "webhook_url": "http://localhost:5678/webhook/clientmail-send",
  "webhook_secret": "the-same-secret-you-put-in-the-Validate-node",

  "from_email": "you@gmail.com",
  "from_name": "Your Name",

  "brand": {
    "name": "Your Studio",
    "color": "#2563eb",
    "signoff": "— Your Name",
    "site": "https://yoursite.com"
  },

  "paused": false,
  "allowed_recipients": [],
  "clients": {}
}
```

**`from_email` must be the same Gmail account the app password belongs to.** Gmail rewrites
or rejects a From header that isn't the authenticated account, so a mismatch here either
silently changes the sender or fails the send.

**`allowed_recipients` is empty by default, which means it can email anyone.** That is the
normal setting — you approve every send by hand, so a standing list mostly just interrupts
you halfway through a real email. If you do want a hard rail while testing:

```bash
clientmail allow you@yourdomain.com   # only this address
clientmail allow any                  # lift it again
clientmail allow                      # show the current setting
```

---

## 6. Prove it works

```bash
clientmail check --ping
```

Expect `reachable  HTTP 200`. That proves the URL is right, the workflow is active, and the
secret matches. It sends no email.

```bash
clientmail test-email you@yourdomain.com
```

Open it **on your phone as well as your laptop** and check: not in spam, sender name right,
bold/bullets/link all render, no n8n attribution line.

Then do some work in any repo and type:

```
/gmailsum
```

If `/gmailsum` isn't offered, restart Claude Code — skills load at startup.

---

## If the import came out wrong

Node formats change between n8n versions. If something imports empty, build these six by
hand:

| Node | Type | Settings |
|---|---|---|
| Webhook | Webhook | Method `POST`, Path `clientmail-send`, Respond `Using 'Respond to Webhook' node` |
| Validate | Code | Paste the JS from the `Validate` node in the JSON file |
| Is probe? | If | `{{ $json.probe }}` → Boolean → is true |
| Respond Probe | Respond to Webhook | Respond With `JSON`, body `{{ JSON.stringify({ ok: true, probe: true }) }}` |
| Send Email | Send Email | see below |
| Respond Sent | Respond to Webhook | Respond With `JSON`, body `{{ JSON.stringify({ ok: true, accepted: true }) }}` |

**Send Email**: From `{{ $json.from }}`, To `{{ $json.to }}`, Subject `{{ $json.subject }}`,
Email Format `Both`, Text `{{ $json.text }}`, HTML `{{ $json.html }}`; Options → CC
`{{ $json.cc }}`, BCC `{{ $json.bcc }}`, Reply To `{{ $json.replyTo }}`, Attachments `{{ $json.attachmentFields }}`, **Append Attribution off**.

Wiring: `Is probe?` true → Respond Probe, false → Send Email → Respond Sent.

---

## Troubleshooting

Run `clientmail doctor` first — Python, git, install paths, skills, recent sends.

| Symptom | Cause |
|---|---|
| `404` "webhook is not registered" | Workflow not published (**Publish** button in n8n 2.x, **Active** toggle in 1.x), you used the Test URL, or n8n isn't running |
| Connection refused | `npx n8n` was stopped — that terminal window closed or the machine rebooted |
| `401` / "secret does not match" | `webhook_secret` ≠ `SECRET` in the Validate node |
| `Invalid login` / `535` | Wrong app password, or you used your normal Google password |
| Email arrives from the wrong address | `from_email` isn't the account the app password belongs to |
| "sent automatically with n8n" | Append Attribution still on in the Send Email node |
| Attachments missing | Options → Attachments must be `{{ $json.attachmentFields }}` (in some n8n versions this option is called *Attachments (File)*) |
| Raw HTML in the body | Email Format is `Text`, should be `Both` |
| Recipient refused | You set a restriction. Run `clientmail allow any` to lift it |
| Sending refused entirely | `"paused": true`, or `from_email` is unset |
| `/gmailsum` missing | Re-run the installer, then restart Claude Code |
