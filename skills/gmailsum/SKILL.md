---
name: gmailsum
description: Summarise what happened in this Claude Code session into a short non-technical email, draft it, take the user's recipients and edits, and send it through Gmail. Use whenever the user wants to tell someone what was done — a client, a manager, a teammate. Trigger phrases include "/gmailsum", "email this to the client", "send an update about what we did", "summarise this session and mail it", "draft an email about today's work".
---

# /gmailsum — session → email

Turn what actually happened in this session into a short email a non-technical
person can read, get it approved, send it.

**Never send without the user explicitly telling you to.** No exceptions.

The pipeline:

```
1 gather evidence   → session transcript (+ git, if there is any)
2 take extras       → anything the user typed or attached
3 write the draft   → short, plain, old state → new state
4 show it           → content approval
5 ask recipients    → To / CC / BCC, comma-separated
6 confirm & send    → hash-gated
```

---

## 1. Gather the evidence

Call `session_context` with `cwd` set to the working directory.

If you know your own session id (it appears in your scratchpad directory path), pass it
as `session_id` — otherwise the most recently modified transcript for this directory is
used, which is right unless two Claude Code windows are open on the same repo.

You get back: every prompt the user typed (verbatim, deduplicated), files created and
edited, commands run, errors returned, and how long the session ran.

**Use this rather than your memory of the conversation.** Context is lossy — long sessions
get compacted and early detail silently disappears. The transcript is what happened.

Then, if this is a git repo, add `work_status` (when a baseline was pinned with
`/client-work`) or a plain `git diff --stat` for the file-level picture. Git evidence is a
useful cross-check but it is secondary here: the session is the source.

**If `session_context` fails** — no transcript, brand-new session — say so plainly and
build the summary from the conversation instead, telling the user that's what you did.

## 2. Take the user's extras

`/gmailsum` may be invoked with extra text, images, or both. All of it is input:

- **Typed context** — "this was for the Henderson launch", "don't mention the database
  migration". Treat as authoritative; it outranks what you inferred.
- **Images the user attaches to you** — screenshots of the bug, a design, a client's
  message. Read them and let them sharpen the problem statement. These are *context*;
  they do not go in the email unless the user says so.
- **Images for the email** — if the user wants pictures in the message, ask for the file
  paths and put them in the draft's `attachments:` field.

If the session is ambiguous about who the audience is or what they already know, ask one
question before drafting. One — not an interview.

## 3. Write the draft

**Per bullet, this exact shape:** `**Thing they recognise** — was X, now Y.`

The old state is not optional. "Search now handles partial words" tells them nothing;
"searching *invo* used to return nothing — it now finds *Invoice* as you type" tells them
you fixed the thing that annoyed them.

**Never write these words:**

| Don't write | Write instead |
|---|---|
| refactored, rewrote, cleaned up | usually: don't mention it at all |
| endpoint, API, route, backend | name the feature they see |
| cache, cached | "remembers, so it loads instantly next time" |
| race condition, deadlock | "two things happening at once could…" |
| null, undefined, NaN | "blank", "missing" |
| async, promise, thread, queue | omit — describe the visible effect |
| component, module, function, class | "the checkout page", "the search box" |
| deployed, merged, pushed, shipped to prod | "live" |
| regex, query, schema, migration | name what they see changing |
| latency, throughput, O(n) | "speed", "faster" |
| exception, stack trace, 500 | "error message", "crash" |
| repo, branch, commit, PR | omit entirely |

Test each sentence: **would someone who has never seen a terminal understand it, and would
they care?** If no to either, rewrite or cut.

**Rules that keep it honest:**

- **Every claim traces to the evidence.** A file in `files_created`, a command in
  `commands`, a prompt in `user_prompts`. If you cannot point at it, it does not go in —
  no "also improved performance", no "various fixes".
- **Errors in the session are not automatically failures.** A command that failed and was
  then fixed is not a problem to report. A thing that is still broken is.
- **Unfinished work gets said out loud.** If something was asked for and not delivered,
  the email says so. Silence implying "done" is the worst outcome this tool can produce.
- **Never invent** dates, test coverage, or next steps nobody agreed to.

**Hard limits — this is meant to be very short:**

- Subject ≤ 60 characters, states the outcome. Not "Project update" — "Search fixes are live".
- Opening: one sentence.
- Bullets: 2–5, each ≤ 25 words.
- Closing: one sentence — what you need from them, or explicitly that you need nothing.
- **Body under 150 words.** Count. Over it means you're explaining, not reporting.

## 4. Write the file and show it

Get `drafts_dir` from `config_check`. Write to
`<drafts_dir>/YYYY-MM-DD-<who>-<slug>.md`.

**Leave `to:` empty at this stage.** Content first, recipients second.

```markdown
---
to:
cc:
bcc:
subject: Search fixes are live
template: client-update
attachments:
---

Hi Jane,

The search problems you flagged are fixed and live.

## What's different

- **Partial words** — searching "invo" used to return nothing; it now finds *Invoice* as you type.
- **Typing lag** — the page froze while you typed; it keeps up now.

Nothing needed from you — have a look when you get a minute.
```

Frontmatter fields: `to`, `cc`, `bcc`, `subject`, `template`, `client`, `reply_to`,
`from_name`, `attachments`. All of `to`/`cc`/`bcc`/`attachments` are comma-separated lists.
Body is markdown: `##`, `-`, `**bold**`, `*italic*`, `[text](url)`, `` `code` ``.

The sign-off comes from the template — don't type "Best, Karan" into the body or it appears
twice.

Call `draft_render`, then show the user `text_preview` verbatim in a code block:

```
Draft: <path>

Look right? Tell me what to change, or say "looks good" and I'll ask who it goes to.
```

**Stop and wait.** Do not proceed to recipients in the same turn.

## 5. Ask for the recipients

Once the content is approved, ask for all three in one go:

```
Who should this go to?

  1. To   — the main recipient(s)
  2. CC   — anyone who should see it (optional)
  3. BCC  — anyone who should see it without the others knowing (optional)

Several people on any line: separate them with commas.
Say "none" or leave blank for CC and BCC.
```

Wait for the answer. Then write the addresses into the frontmatter.

- Bare addresses only — `jane@acme.com`, not `Jane <jane@acme.com>`. The server rejects
  the second form rather than mangling names that contain commas.
- Don't invent or auto-fill an address from the session. If the user gives a name instead
  of an address, ask for the address. Mailing the wrong person is not recoverable.
- An address in both To and CC is rejected — the person would get two copies.

## 6. Attachments

Put absolute paths in `attachments:`. Allowed: png, jpg, jpeg, gif, webp, svg, bmp, pdf,
txt, csv, md, log, json, zip. Total 8 MB.

Images are sent as **attachments, not inline in the message body**. That is not a
limitation you should try to work around: Gmail strips `data:` URI images, so an inline
screenshot renders as nothing for the recipient. If the body refers to a picture, word it
as "screenshot attached".

## 7. Render again, then send

Call `draft_render` once more — the file changed, so the hash changed. Show the user the
final headers and get the last confirmation:

```
To:      jane@acme.com, ops@acme.com
CC:      —
BCC:     —
Subject: Search fixes are live
Attached: before.png, after.png

Send it?
```

On an explicit yes, call `email_send` with:

- `draft_path`
- `confirm_hash` — from the render **they just saw**
- `confirm_recipient` — every To: address, comma-separated

The server refuses if the file changed since that render, if there are no recipients, or
if `confirm_recipient` doesn't match. **Never work around a refusal** — re-render, show
the user, ask again.

Report back plainly: sent, to whom, subject, attachments. If n8n returned an error, show it
verbatim. A failed send reported as a success is the worst thing this tool can do.

---

## Revisions

- **User prompts a change** ("warmer", "cut the last bullet", "mention the invoice bug"):
  edit the file, re-render, show it again.
- **User edited the file themselves:** re-render and show the result before sending — you
  need the new hash, and they may have introduced a typo.
- **Different look:** `templates_list`, then set `template:` in the frontmatter.

## Making a template

If the user pastes a sample email or wants their own branding, write
`<name>.html` (and optionally `<name>.txt`) into the templates dir from `templates_list`.

Placeholders: `{{ subject }}`, `{{ preheader }}`, `{{ body_html }}`, `{{ body_text }}`
(txt only), `{{ brand_name }}`, `{{ brand_color }}`, `{{ brand_signoff }}`,
`{{ brand_site }}`, `{{ year }}`. Unknown placeholders render empty.

Not stylistic preferences — these are what make mail render at all:

- **Every style inline.** Gmail strips `<style>` blocks on forward; Outlook renders with Word.
- **Tables for layout**, `role="presentation"`, max-width 600px. Outlook has no flexbox or grid.
- **No external CSS, web fonts, or JavaScript.**
- Start from `client-update.html` rather than from scratch, and have the user send
  themselves a test — a render is not proof it survives a real mail client.

## When something is wrong

- `config_check` reports problems → fix config first; don't draft an email that can't be sent.
- `paused: true` → sending is deliberately off. Tell the user; don't edit their config to
  get around it.
- Recipient blocked by `allowed_recipients` → say which address and why. That list is a
  safety rail they set on purpose.
