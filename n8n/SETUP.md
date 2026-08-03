# n8n setup — about 5 minutes

This is the only part that isn't automated, and it's a one-time job. n8n holds the Gmail
connection so you never deal with Google Cloud projects, `client_secret.json`, or OAuth
consent screens.

## 1. Get an n8n

Either works:

- **n8n Cloud** (n8n.io) — easiest. Its Gmail node uses n8n's own Google OAuth app, so
  connecting is literally "Sign in with Google". No Google Cloud project at all.
- **Self-hosted** (`docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n`)
  — free, but the Gmail node will ask you to supply your own Google OAuth client ID and
  secret, because there's no n8n-owned app backing it. That's a Google Cloud project after
  all. **If you want to skip Google Cloud entirely, use n8n Cloud.**

## 2. Import the workflow

Workflows → **Import from File** → pick `clientmail-send.workflow.json`.

You should get six nodes in a line:

```
Webhook → Validate → Is probe? ─┬─ true ──→ Respond Probe
                                └─ false ─→ Send via Gmail → Respond Sent
```

## 3. Set your secret

Open the **Validate** node. Line 4:

```js
const SECRET = 'REPLACE_WITH_YOUR_SECRET';
```

Replace it with a long random string. Generate one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the **same** string in `~/.clientmail/config.json` as `webhook_secret`.

Without this, anyone who discovers your webhook URL can send email from your Gmail account.

## 4. Connect Gmail

Open **Send via Gmail** → Credential to connect with → **Create new** → sign in with the
Google account you want to send from → allow.

Check that **Options → Append n8n Attribution** is **off**. The node otherwise appends
"This email was sent automatically with n8n" to the bottom of every message, which is not
what you want on a client update. The imported workflow sets this off already — confirm it
survived the import.

## 5. Activate and copy the URL

Toggle the workflow **Active** (top right). Open the **Webhook** node and copy the
**Production URL** — it looks like:

```
https://yourname.app.n8n.cloud/webhook/clientmail-send
```

⚠️ Copy the **Production** URL, not the **Test** URL. The Test URL only works for one
execution after you press "Listen for test event", and it is the single most common reason
sending fails with a 404.

Paste it into `~/.clientmail/config.json` as `webhook_url`.

## 6. Prove it works

```bash
clientmail check --ping
```

`"reachable": true` means n8n received the request and your secret matched.

Then send yourself a real one before you ever point this at a client:

```bash
clientmail test-email you@yourdomain.com
```

Open it on your phone as well as your laptop. A render is not proof.

---

## If the import didn't come out right

Node parameter formats change between n8n versions, so if a node imports with empty or odd
settings, build these six by hand — it takes a few minutes:

| Node | Type | Settings |
|---|---|---|
| Webhook | Webhook | Method `POST`, Path `clientmail-send`, Respond `Using 'Respond to Webhook' node` |
| Validate | Code | Paste the JS from the `Validate` node in the JSON file |
| Is probe? | If | Condition: `{{ $json.probe }}` — Boolean → is true |
| Respond Probe | Respond to Webhook | Respond With `JSON`, body `{{ JSON.stringify({ ok: true, probe: true }) }}` |
| Send via Gmail | Gmail | Resource `Message`, Operation `Send`, To `{{ $json.to }}`, Subject `{{ $json.subject }}`, Email Type `HTML`, Message `{{ $json.html }}`; Options: CC `{{ $json.cc }}`, BCC `{{ $json.bcc }}`, Reply To `{{ $json.replyTo }}`, Sender Name `{{ $json.fromName }}`, Append Attribution **off** |
| Respond Sent | Respond to Webhook | Respond With `JSON`, body `{{ JSON.stringify({ ok: true, messageId: $json.id \|\| '' }) }}` |

Wire `Is probe?` **true** output to Respond Probe and **false** output to Send via Gmail.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `404` from n8n | Workflow not Active, or you used the Test URL |
| `401` / "secret does not match" | `webhook_secret` ≠ the `SECRET` in the Validate node |
| Send succeeds, no email arrives | Gmail credential is connected to the wrong Google account — check the Executions tab |
| Client sees "sent automatically with n8n" | Append Attribution is still on in the Gmail node |
| Email arrives as raw HTML source | Email Type is `Text`, should be `HTML` |
