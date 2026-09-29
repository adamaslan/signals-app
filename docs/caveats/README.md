# Caveats

Session-scoped notes on work that shipped but isn't as solid as it looks —
unverified edges, failures merged past, scope dropped, arbitrary constants.
Not an action-item list (that's `docs/manual-todo.md`) and not a forward
handoff (that's `docs/session-handoff.md` when one exists). A caveat may have
no owner and may never be fixed; it exists so the next session doesn't mistake
"merged" for "verified".

## Layout

```
docs/caveats/
  README.md                      ← this file, index table below
  YYYY-MM-DD-<2-4-keywords>.md   ← one session, one file, never appended to
```

## Secret policy

No API keys, tokens, passwords, session cookies, webhook secrets, connection
strings, project IDs, hostnames, or email addresses — including the user's
own. Name the variable or the file that holds it instead. Env var *names* are
fine; env var *values* never appear.

## Index

| Date | Entry | Repo(s) | Subject |
|---|---|---|---|
| 2026-09-29 | [pr39-rebase-coderabbit](2026-09-29-pr39-rebase-coderabbit.md) | signals-app | PR #39 (/v1 integration API) rebase + 2 CodeRabbit rounds; cloud signal-scan never exercises the new /v1 code path |
