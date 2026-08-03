---
name: client-work
description: Start a tracked piece of client work. Pins the repo's current git SHA and records the client's request verbatim, so the update email that goes out later is built from the real diff instead of from memory. Use at the START of work a client asked for — before writing any code. Trigger phrases include "client wants", "new client work", "log this request", "start client work".
---

# Start tracked client work

You are pinning a baseline. Do this **before** touching any code. Two minutes here is
what stops the eventual client email from being a guess.

## 1. Get the request verbatim

Copy the client's words exactly as given — the Slack message, the email, the voice-note
transcript. Do **not** tidy it, summarise it, or turn it into a task list. The verbatim
text is what you will check the finished work against, and clients notice when you solve
a neater problem than the one they described.

If the user paraphrased rather than pasting, ask once for the original wording. If they
don't have it, record their paraphrase and note `(paraphrased)` in the request text.

## 2. Identify the client

Check `config_check` for configured client keys. Use an existing key when one matches.
If this is a new client, tell the user the key doesn't exist yet and that you'll use their
email address for now — they can add a proper entry to `config.json` later.

## 3. Check the tree is clean first

Run `git status`. If there are uncommitted changes already sitting there, say so and ask
whether to commit or stash them first. This matters: the baseline can't tell pre-existing
mess apart from your work, so anything left dirty now will show up in the client's email
as something you did.

## 4. Pin it

Call `work_start` with `repo_path`, `client`, `request` (verbatim), and a short `title`.

Then confirm back, briefly:

```
Tracking: <title>
Client:   <name> <email>
Baseline: <short sha> on <branch>
Request:  "<first line of the request>"
```

## 5. Do the work

Nothing about this skill changes how you work. Build the thing properly.

When it's done, `/client-update` turns the diff since this baseline into the email.

## Notes

- One active session per repo. Starting a new one replaces the old baseline — if there's
  already a session for this repo, say what it was and confirm before overwriting it.
- You don't have to commit for this to work; `work_status` reports uncommitted and
  untracked changes too.
