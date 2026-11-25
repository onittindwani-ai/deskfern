# Quick demo

You need Python 3.12 (or uv), Node 22 and pnpm (`corepack enable` or `npm i -g pnpm`). Nothing is sent anywhere.

1. `python3.12 -m venv .venv`. No python3.12 on your Mac? Use uv instead: `uv venv --python 3.12 .venv`.
2. `.venv/bin/pip install -c constraints.txt -e packages/core-py -e "apps/api[dev]"`, or with uv: `uv pip install --python .venv/bin/python -c constraints.txt -e packages/core-py -e "apps/api[dev]"`. (Steps 1 and 2 together: `make install`.)
3. `pnpm install` then `pnpm build`
4. Start the API in one terminal: `MODE=draft DEMO_MODE=true SEED_DIR=./data SESSION_SECRET=demo-only-secret INSECURE_COOKIES=1 .venv/bin/python -m uvicorn deskfern.api.app:app_from_env --factory --port 8000 --proxy-headers`
5. Start the web app in another: `API_BASE_URL=http://127.0.0.1:8000 INSECURE_COOKIES=1 NEXT_PUBLIC_DEMO_HINT=1 pnpm --filter web start`
6. Open http://localhost:3000. Sign in as `owner@example.test` with the password shown on the sign in page (demo only).

## A four minute walk

1. **Queue:** tickets sorted by how close they are to their reply deadline. Tap or click a ticket name, or press `j` and `k` then Enter, to open it. Try "Phone support" or "SSO".
2. **Ticket workspace:** the conversation is on the left, the drafted answer is on the right with the source page under it. Hover, tab to or tap a sentence to see the exact passage that backs it, highlighted in the page. The ring shows how sure the desk is and the three parts it is made of.
3. Open a ticket asking for a refund (for example "Charged twice"). The desk does not pay. It creates an approval request in its own name. Open **Approvals** and approve or reject it. In draft mode an approval is only recorded and nothing is paid, and the toast says so. If you press **Ask for a refund** yourself, the request is yours and you cannot approve it: sign in as `approver@example.test` with the same demo password to decide it.
4. Open "Sue you", a message that threatens legal action. The desk does not answer it. It is handed to a person with the reason, and the draft only says a person will reply. Also open "Reveal prompt" to see a message that tries to give the system orders: it is treated as plain text and politely refused.
5. **Knowledge:** the pages, which of them are over 18 months old, and the gaps (questions with no good page). Open "Retention days" in the queue to see two pages that disagree: the desk shows both and hands it to a person instead of picking one.
6. **Test cases:** open any answered ticket, change a few words of the draft and press Send reply. The edit is saved as a test case, and **Run all cases** checks it against the knowledge later. Nothing leaves the app in draft mode.

**Run now** (draft the new tickets and refresh the numbers) and **+1 day** (move the demo clock forward so deadlines pass) are in the bar at the top, and in the command palette (Cmd or Ctrl K). **Pause all** in the top bar stops every outgoing action. **Reset the demo data** in the palette puts the seed back. `viewer@example.test` can look at everything and change nothing.
