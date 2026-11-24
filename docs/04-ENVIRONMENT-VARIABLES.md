# 04 Environment variables

Every variable, in plain words. Values are never printed by any tool in this repo. `python3 tools/doctor.py` checks them and `make check-env` compares your file with `.env.example`.

Where to put them: **local** in a `.env` file at the repo root (copied from `.env.example`); **Vercel** in the project's Environment Variables (web variables only); **Railway** in each service's Variables tab; **Hetzner** in the `.env` file next to `docker-compose.yml` on the server. Steps that name a vendor page were not all re-checked today: those marked UNVERIFIED must be checked against the vendor's current docs before you rely on them.

## SESSION_SECRET

- Required: required
- What it does: Signs the login cookie. A long random string only you know.
- How to get it:
  1. Run `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
  2. Paste the result as the value.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: A short or reused value makes sessions guessable. `make check-env` warns if it is left on the placeholder.

## DATABASE_URL

- Required: required
- What it does: Where data is stored. SQLite file for the demo, Postgres for real use.
- How to get it:
  1. Demo: leave `sqlite:///./deskfern.sqlite3`.
  2. Postgres: create a database, then use `postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME`.
- Cost: SQLite is free. Postgres is part of your hosting bill.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `python3 tools/doctor.py` prints `FAIL database` with the error type.

## MODE

- Required: optional
- What it does: `draft` (default) sends nothing and pays nothing. `live` allows replies and refunds after approval.
- How to get it:
  1. Leave `draft` until the go live checklist (doc 09) is done.
  2. Change to `live` on the server only.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: In `live` with DEMO_MODE on, the doctor warns.

## DEMO_MODE

- Required: optional
- What it does: `true` uses seeded fictional data and offline adapters only. Turn it off for a real client.
- How to get it:
  1. Set `false` on a real install, then set OWNER_PASSWORD_HASH.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: With demo off and no password hash the doctor says OWNER_PASSWORD_HASH is empty.

## KILL_SWITCH

- Required: optional
- What it does: `true` stops every reply and refund at once.
- How to get it:
  1. Set `true` to pause. The owner can also press Pause all in the app.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: Writes raise a kill switch message and nothing is sent.

## OWNER_EMAIL / OWNER_PASSWORD_HASH

- Required: required when DEMO_MODE is off
- What it does: The owner login. The app stores a hash, never the password.
- How to get it:
  1. Run `python3 tools/hash_password.py` and type the password when asked.
  2. Put the printed hash in OWNER_PASSWORD_HASH.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: Sign in says the email or password is wrong.

## APPROVER_EMAIL / APPROVER_PASSWORD_HASH

- Required: optional, but needed to approve refunds you asked for
- What it does: A second login that can approve refunds. The person who asks cannot approve their own request.
- How to get it:
  1. Make a hash the same way as the owner.
  2. Give the approver their own password.
  3. For someone who may only look (an accountant, a client who watches the queue), repeat with `VIEWER_EMAIL` and `VIEWER_PASSWORD_HASH`. That login opens every page and changes nothing.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: An approval you requested says the requester cannot decide it.

## HELPDESK_ADAPTER / INBOX_ADAPTER / EMAIL_ADAPTER / PAYMENTS_ADAPTER

- Required: optional
- What it does: Which integration is used in each category. `seed` means offline.
- How to get it:
  1. Pick a name from the Integrations page.
  2. Set the variables of that integration below.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: The doctor says which variables are missing for the chosen adapter.

## KNOWLEDGE_DIR

- Required: optional
- What it does: A folder of Markdown, text and PDF files read as the knowledge base.
- How to get it:
  1. Put your files in a folder on the server.
  2. Set the path, then press Sync on the Knowledge page or restart the demo.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: An empty folder gives an empty knowledge base and every ticket is handed to a person.

## SLA_URGENT_H, SLA_HIGH_H, SLA_NORMAL_H, SLA_LOW_H

- Required: optional
- What it does: First reply targets in hours. These are decided defaults (urgent 1, high 4, normal 24, low 72), a starting point you can change for each client.
- How to get it:
  1. Agree the targets with the client.
  2. Set the four numbers.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: The queue timers use these numbers.

## SLA_CLOCK, BUSINESS_HOURS_START, BUSINESS_HOURS_END, BUSINESS_DAYS, OFFICE_TIMEZONE

- Required: optional
- What it does: Which hours count towards a first reply target. The default is `business`: 09:00 to 17:00 on Monday to Friday in `OFFICE_TIMEZONE` (`America/New_York`). A ticket that arrives when the office is closed starts its clock at the next opening, so a normal ticket (24 hours) that arrives on Friday at 16:00 is due on Wednesday at 16:00 (8 hour days). Set `SLA_CLOCK=round_the_clock` to count every hour of every day instead (then the other four are ignored).
- How to get it:
  1. Ask the client when the support team really answers.
  2. Set the start and end hour (whole hours, 0 to 24), the days as three letter names separated by commas, and the time zone name such as `Europe/London`.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` reports a wrong day name, a wrong time zone or an end hour that is not after the start hour.
- If it is wrong: The doctor prints a PROBLEM line. Tickets are never created with a wrong deadline silently.

## LLM_BACKEND

- Required: optional
- What it does: Where the model jobs run (reply sentences, the sentence judge, the triage second look, help page suggestions and the Insights wording). `offline` (the default) is a built in rules stand in, not a language model, and needs no account. `fixture` replays recorded answers from `LLM_FIXTURES_DIR` (the repo ships `data/llm_fixtures/`, recorded from a local fake server, not from a real model). `anthropic` calls a real model and needs `ANTHROPIC_API_KEY`; it has not been run against the real service (UNVERIFIED). Whatever writes the sentences, the code groundedness check runs on them and unsupported sentences are removed. A model can never refund, approve or send. See `facts/models.md`.
- How to get it:
  1. Leave it as `offline` to try the product.
  2. For `anthropic`, create a key in the Anthropic console and set `ANTHROPIC_API_KEY`.
- Cost: `offline` and `fixture` are free. `anthropic` is charged per use by the provider.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host does not need it.
- Check it: `python3 tools/doctor.py` prints `llm_backend=` and a PROBLEM line when `anthropic` has no key.
- If it is wrong: The doctor names the missing key.

## JUDGE

- Required: optional
- What it does: `rules` (the default) means only the code checks decide whether a reply sentence is supported. `model` lets the model look at a sentence whose hard checks passed (numbers, units, names, yes or no) but whose word match is in a gray band, and rescue it. The model must give an exact quote from a help page and the code checks the quote again. With `LLM_BACKEND=offline` the stand in never rescues anything. Not run against a real model (UNVERIFIED).
- How to get it: Leave it as `rules`. Set `model` only after `LLM_BACKEND=anthropic` works for you.
- Cost: `model` adds one model call for each gray sentence.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host does not need it.
- Check it: `python3 tools/doctor.py` prints `judge=`.
- If it is wrong: Any other value stops the app at start with a clear message.

## TRIAGE_ASSIST

- Required: optional
- What it does: `off` (the default) means the rules alone read each ticket. `model` adds a model's second look: it may add a warning flag (legal, safety, security, outage, another customer's data, instructions aimed at the system) and raise the urgency. It never removes a flag, never lowers urgency and never answers the ticket. Not run against a real model (UNVERIFIED).
- How to get it: Leave it `off`, or set `model` once `LLM_BACKEND=anthropic` works for you.
- Cost: One small model call for each new ticket.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host does not need it.
- Check it: `python3 tools/doctor.py` prints `triage_assist=`.
- If it is wrong: Any other value stops the app at start with a clear message.

## LOGIN_BURST

- Required: optional
- What it does: How many sign in tries one address and email may make in a row before a short wait (one more every 12 seconds). Leave it at 5 outside tests; the browser tests raise it because every test signs in from one address.
- How to get it: Not needed.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host does not need it.
- Check it: `python3 tools/doctor.py`.
- If it is wrong: Sign in answers "too many tries" sooner than expected.

## CRAWL_MAX_PAGES, CRAWL_MAX_DEPTH, CRAWL_MAX_BYTES, CRAWL_DELAY_S

- Required: optional
- What it does: Limits for the site crawl.
- How to get it:
  1. Keep the defaults for a small help site.
  2. Raise them slowly.
- Cost: Free. A bigger crawl uses more of your server time.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: Skipped pages are listed with the reason on the Knowledge page.

## CHATWOOT_BASE_URL, CHATWOOT_API_TOKEN, CHATWOOT_ACCOUNT_ID, CHATWOOT_WEBHOOK_SECRET

- Required: required for Chatwoot
- What it does: Where Chatwoot lives, your access token, the account number and the secret that signs webhooks.
- How to get it:
  1. Open your Chatwoot, then your profile settings, and copy the access token (page names UNVERIFIED, check the current Chatwoot docs).
  2. The account number is in the address of the dashboard.
  3. In the webhook settings add `https://YOUR-API/api/webhooks/chatwoot`, tick message created, and copy the secret (the signing rule itself was read from the Chatwoot help article on 2026-11-23).
  4. Put the four values in the API environment.
- Cost: Free if self hosted, a plan if hosted. Not read today.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: A wrong secret gives HTTP 401 on the webhook. A wrong token gives `Chatwoot refused the key (HTTP 401)`.

## INTERCOM_TOKEN, INTERCOM_ADMIN_ID

- Required: required for Intercom
- What it does: An access token and the id of the admin replies come from.
- How to get it:
  1. In Intercom's developer hub create an app and copy its access token (UNVERIFIED steps).
  2. Copy an admin id from the team settings.
- Cost: Per the plan. Not read today.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `Intercom refused the key`.

## ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_TOKEN

- Required: required for Zendesk
- What it does: Your Zendesk address, an agent email and an API token.
- How to get it:
  1. In Zendesk's admin area enable token access and add an API token (UNVERIFIED steps).
  2. Use the agent's email.
- Cost: Per the plan. Not read today.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `Zendesk refused the key`.

## FRESHDESK_DOMAIN, FRESHDESK_API_KEY

- Required: required for Freshdesk
- What it does: Your Freshdesk address and your API key.
- How to get it:
  1. Open your profile settings in Freshdesk and copy the API key (UNVERIFIED steps).
- Cost: Per the plan. Not read today.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `Freshdesk refused the key`.

## STRIPE_SECRET_KEY

- Required: required for refunds through Stripe
- What it does: A restricted key that can write refunds only.
- How to get it:
  1. Open the Stripe dashboard, Developers, API keys.
  2. Create a restricted key with write access to Refunds and read access to Charges only.
  3. Start with a test mode key and a test charge.
- Cost: Refund handling follows your Stripe pricing. Not read today.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `Stripe refused the key. Use a restricted key that can write refunds only.`

## DRIVE_TOKEN

- Required: required for the Google Drive source
- What it does: A read only access token for Drive.
- How to get it:
  1. Create an OAuth client in the Google Cloud console and authorise the read only Drive scope (UNVERIFIED steps).
  2. Paste the access token.
- Cost: Free for normal use. Not read today.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `Google Drive refused the token.` Tokens expire, so this source needs a refresh step for long runs (not built).

## NOTION_TOKEN

- Required: required for the Notion source
- What it does: An internal integration token.
- How to get it:
  1. Create an internal integration in Notion's integrations page (UNVERIFIED steps).
  2. Share the pages you want read with that integration.
  3. Paste the token.
- Cost: Free for normal use. Not read today.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `Notion refused the token. Share the pages with the integration first.`

## IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM

- Required: required for email intake
- What it does: Mailbox access for incoming support email and the sender address.
- How to get it:
  1. Gmail: turn on two step sign in, then create an app password in your Google account security page (the exact Google page name was not re-checked, UNVERIFIED).
  2. Use the mail host's IMAP and SMTP names and ports.
  3. Use a dedicated support mailbox.
- Cost: Usually part of the mail plan.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `The inbox rejected the login`.

## API_BASE_URL

- Required: required on the web host
- What it does: The private address of the API, used only by the web server.
- How to get it:
  1. Local: `http://127.0.0.1:8000`.
  2. Railway: the private network name of the api service.
  3. Hetzner: `http://api:8000` inside Compose.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: The web app shows `The server is not reachable`.
