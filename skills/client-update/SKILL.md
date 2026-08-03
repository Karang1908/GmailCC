---
name: client-update
description: Turn finished client work into a very short, non-technical update email — what the old state was, what the new state is — then draft it, revise it with the user, and send it through Gmail via n8n. Use after finishing work a client asked for. Trigger phrases include "email the client", "send the client an update", "draft the update email", "tell the client it's done", "client update".
---

# Draft and send a client update

Produce a **short, non-technical** email describing what changed, get the user's approval,
send it. The client is not an engineer. They care about what they can now do that they
couldn't before.

Never send without the user explicitly telling you to. There is no exception to this.

---

## Step 1 — Gather evidence

1. `config_check` — confirms setup and tells you `drafts_dir`.
2. `work_status` with the repo path — returns the client's verbatim request, commits since
   the baseline, files changed (**including uncommitted and untracked**), and the diffstat.

**No session pinned?** Don't guess. Ask the user which git ref the work started from
(`git log --oneline -20` helps them pick), then use `git diff <ref>` directly for the same
evidence. If there's no git history at all, ask them to list what changed and say plainly
in your summary that it's based on their description, not on a diff.

Then read the actual diffs for the changed files. `--stat` tells you *where* work happened;
only the diff itself tells you *what behaviour changed*, and behaviour is the entire email.

## Step 2 — Build the evidence table (internal — never shown to the client)

One row per change. Write it out for yourself before drafting:

| Evidence (file / commit) | What technically changed | Client-visible BEFORE | Client-visible AFTER |
|---|---|---|---|

Then apply three filters:

- **No evidence → delete the row.** If you cannot point at a hunk or a commit, it does not
  go in the email. Not "we also improved performance", not "various fixes". This is the
  single most important rule here: an email that overclaims is worse than one that
  under-describes, because the client will test it.
- **No client-visible change → move it out of the bullets.** Refactors, renames, tests,
  dependency bumps, config. These earn at most one closing half-sentence
  ("plus some tidying under the hood"), and often not even that.
- **Didn't actually finish → say so.** If part of the request is incomplete, it belongs in
  the email as a plain statement of what's still open. Never let silence imply "done".

Finally, check every row in the request from Step 1 is either covered or explicitly listed
as still open. A client update that ignores half of what they asked for reads as a dodge.

## Step 3 — Write it in the client's language

**Per bullet, this exact shape:** `**Thing they recognise** — was X, now Y.`

The old state is not optional. "Search now handles partial words" tells them nothing;
"searching *invo* used to return nothing — it now finds *Invoice* as you type" tells them
you fixed the thing that annoyed them.

**Never write these words:**

| Don't write | Write instead |
|---|---|
| refactored, rewrote, cleaned up | usually: don't mention it at all |
| endpoint, API, route, backend | name the feature the client sees |
| cache, cached | "remembers, so it loads instantly the second time" |
| race condition, deadlock | "two things happening at once could…" |
| null, undefined, NaN | "blank", "missing" |
| async, promise, thread, queue | omit — describe the visible effect |
| component, module, function, class | "the checkout page", "the search box" |
| deployed, merged, pushed, shipped to prod | "live" |
| regex, query, schema, migration | name what the user sees changing |
| latency, throughput, O(n) | "speed", "faster" |
| exception, stack trace, 500 | "error message", "crash" |
| repo, branch, commit, PR | omit entirely |

Test each sentence: **would someone who has never seen a terminal understand it, and would
they care?** If no to either, rewrite or cut it.

**Hard limits — the user asked for very short. Respect it:**

- Subject: ≤ 60 characters. State the outcome. Not "Update on your project" — say
  "Search fixes are live".
- Opening: one sentence.
- Bullets: 2–5. Each ≤ 25 words.
- Closing: one sentence — what you need from them, or explicitly that you need nothing.
- **Whole body under 150 words.** Count them. If you're over, you're explaining rather
  than reporting.

Do not invent: no dates you weren't given, no "tested across all browsers" unless you
tested across all browsers, no next steps nobody agreed to.

## Step 4 — Write the draft file

Path: `<drafts_dir>/YYYY-MM-DD-<client>-<slug>.md`

```markdown
---
to: jane@acme.com
cc:
subject: Search fixes are live
template: client-update
client: acme
---

Hi Jane,

The search problems you flagged are fixed and live.

## What's different

- **Partial words** — searching "invo" used to return nothing; it now finds *Invoice* as you type.
- **Typing lag** — the page froze while you typed; it keeps up now.

Nothing needed from you — have a look when you get a minute.
```

Frontmatter accepts only: `to`, `cc`, `bcc`, `subject`, `template`, `client`, `reply_to`,
`from_name`. `cc`/`bcc` are comma-separated. Body is markdown: `##` headings, `-` bullets,
`**bold**`, `*italic*`, `[text](url)`, `` `code` ``.

The sign-off comes from the template — don't type "Best, Karan" into the body or it will
appear twice.

## Step 5 — Show it and stop

Call `draft_render`. Show the user the `text_preview` verbatim in a code block, then:

```
To: jane@acme.com   Subject: Search fixes are live
Draft: <path>

Reply "send" to send it, tell me what to change, or edit the file directly.
```

**Now stop and wait.** Do not call `email_send` in the same turn you drafted. The user
seeing the content is the point of the whole system.

## Step 6 — Revisions

- **User prompts a change** ("warmer", "cut the last bullet", "mention the invoice bug"):
  edit the draft file, `draft_render` again, show it again. The hash changes; that's correct.
- **User edited the file themselves:** `draft_render` again and show the result before
  sending — you need the new hash, and they may have introduced a typo.
- **User wants a different look:** `templates_list`, then set `template:` in the frontmatter.

## Step 7 — Send

Only when the user has clearly said to send. Then `email_send` with:

- `draft_path`
- `confirm_hash` — from the `draft_render` **they saw**
- `confirm_recipient` — the To: address, so it's visible in the approval prompt

The server refuses if the file changed since that render, or if the recipient doesn't
match. If it refuses: re-render, show the user what it says now, and ask again. Never
work around a refusal.

Report back plainly: sent, to whom, subject. If n8n returned an error, show it verbatim —
a failed send that reads as a success is the worst outcome this tool can produce.

---

## Making a new template

When the user pastes a sample email or wants their own branding, write a new
`<name>.html` (and optionally `<name>.txt`) into the templates dir from `templates_list`.

Placeholders: `{{ subject }}`, `{{ preheader }}`, `{{ body_html }}`, `{{ body_text }}`
(txt only), `{{ brand_name }}`, `{{ brand_color }}`, `{{ brand_signoff }}`,
`{{ brand_site }}`, `{{ year }}`. Unknown placeholders render as empty string.

Rules that are not stylistic preferences — they are what makes mail render at all:

- **Every style inline.** Gmail strips `<style>` blocks when a message is forwarded, and
  Outlook's Word renderer ignores most of a stylesheet.
- **Tables for layout**, `role="presentation"`, max-width 600px. Outlook has no flexbox
  or grid.
- **No external CSS, no web fonts, no JavaScript.** Use a system font stack.
- Start from `client-update.html` rather than from scratch.

Send a test to the user's own address first — `templates_list` shows the path, and a
render is not proof it survives a real mail client.

## When something is wrong

- `config_check` reports problems → fix config before drafting; don't write an email that
  can't be sent.
- `paused: true` in config → sending is deliberately disabled. Tell the user; don't edit
  their config to get around it.
- Recipient blocked by `allowed_recipients` → tell the user which address and why. That
  list is a safety rail they set on purpose.
