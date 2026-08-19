---
Created: 2026-08-19
Purpose: one-time terminal steps to get SUPABASE_ACCESS_TOKEN into .env so
         the Supabase CLI can be driven non-interactively for phases 2-3
         of docs/backend-state-and-supabase-plan.md.
---

# Supabase Token Setup

Three steps, all in your own terminal — the token never passes through chat.

## 1. Generate a token

Open https://supabase.com/dashboard/account/tokens in a browser, click
**Generate new token**, name it `signals-app-cli`, copy it (you'll only see
it once).

## 2. Open the terminal in VS Code and go to the repo

In VS Code, open a terminal (`` Ctrl+` `` / Terminal → New Terminal). It
likely starts in `homebase` — navigate from there:

```bash
cd ~/code/signals-app
```

(If your VS Code terminal opens somewhere else, use
`cd /Users/adamaslan/code/signals-app` instead — same destination either
way.)

## 3. Run the setup script

```bash
bash scripts/set_supabase_token.sh
```

It will prompt:

```
Paste your Supabase access token (input hidden):
```

Paste the token from step 1 and press Enter. Nothing is echoed to the
screen — that's expected, it's a hidden-input prompt (like a `sudo`
password prompt). The script writes it straight to `.env` (already
gitignored) and confirms:

```
Saved to /Users/adamaslan/code/signals-app/.env (value not displayed).
```

## 4. Tell me it's done

Once you see that confirmation, let me know in chat — I'll pick up
immediately with the Supabase project creation and schema push. I only
check that the variable *name* exists in `.env`, never its value.

---

## If something goes wrong

- **"WARNING: .env is not in .gitignore"** — stop, don't proceed; tell me,
  this would mean the repo's `.gitignore` changed unexpectedly.
- **Script not found** — confirm you're in `/Users/adamaslan/code/signals-app`
  (step 2), not a different directory.
- **Wrong token / need to redo** — just re-run the script; it replaces an
  existing `SUPABASE_ACCESS_TOKEN=` line rather than duplicating it.
