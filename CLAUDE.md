# Deskfern: notes for Claude Code

Cited answer support desk. Python package `deskfern` in `apps/api/src`, tables prefixed `lh_`, base in `packages/` (do not edit). Web in `apps/web`.

- Never send, post or pay in this repo's tests. Draft mode is the default.
- The answer writer is offline and rule based. Do not describe it as a language model.
- Held out evaluation files in `data/eval` must not be tuned against. Keep `heldout_first_run.json` untouched.
- Run `make test`, `python3 tools/check_docs.py` and `python3 tools/scan_secrets.py` after changes.
- No dashes in docs, no private links, GitHub links are repo home pages only.
- The approval requester must never be able to decide their own approval.
