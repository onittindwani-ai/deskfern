# 04 Environment variables

Every variable, in plain words. Values are never printed by any tool in this repo. `python3 tools/doctor.py` checks them and `make check-env` compares your file with `.env.example`.

Where to put them: **local** in a `.env` file at the repo root (copied from `.env.example`); **Vercel** in the project's Environment Variables (web variables only); **Railway** in each service's Variables tab; **Hetzner** in the `.env` file next to `docker-compose.yml` on the server. Steps that name a vendor page were not all re-checked today: those marked UNVERIFIED must be checked against the vendor's current docs before you rely on them.

## SESSION_SECRET

- Required: required
- What it does: Signs the login cookie. A long random string only you know, 32 or more characters. The app and the worker refuse to start on the placeholder from `.env.example` or on a short value (under 32 characters) whenever DEMO_MODE is off, an owner password (OWNER_PASSWORD_HASH) is set, or MODE is live. The only start that keeps the example value is the offline demo with no owner password and MODE draft, because nothing real is behind it. With the example value anyone who has read the public code can make a session token and sign in as the owner.
- How to get it:
  1. Run `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
  2. Paste the result as the value.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: The API and the worker stop at start and name the problem. A short or reused value would make sessions guessable.

## DATABASE_URL

- Required: required
- What it does: Where data is stored. SQLite file for the demo or a single desk, Postgres for real use. Docker compose builds it for you from POSTGRES_PASSWORD.
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
- What it does: `true` loads the fictional world and switches on the public demo logins (the demo password, the reset, tick and advance buttons). The moment OWNER_PASSWORD_HASH is set, every one of those shortcuts is off even if this stays `true`. The default in `.env.example` is `false`.
- How to get it:
  1. Set `false` on a real install, then set OWNER_PASSWORD_HASH.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: With demo off and no password hash the app does not start and the doctor says OWNER_PASSWORD_HASH is empty. With a hash set and demo still on, the doctor prints a PROBLEM line that the demo is on.

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
- What it does: The owner login. The app stores a hash, never the password. OWNER_EMAIL must be your own address: the example address is refused outside the demo, and in live mode.
- How to get it:
  1. Run `python3 tools/hash_password.py` and type the password when asked.
  2. Put the printed hash in OWNER_PASSWORD_HASH inside single quotes.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: Sign in says the email or password is wrong.

## APPROVER_EMAIL / APPROVER_PASSWORD_HASH

- Required: optional in draft mode; required when MODE is live and PAYMENTS_ADAPTER is not seed (the app then refuses to start without it, and without its hash, or if it is the owner's own address)
- What it does: A second login that can approve refunds. The person who asks cannot approve their own request. A refund that someone asked for by hand, or by pressing Draft again or Confirm sender, is asked in that person's name, so they cannot decide it. A refund the worker asks for by itself is asked in Deskfern's own name, so the owner or the approver can decide it; the second login is what makes sure a person other than the owner exists.
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
- What it does: A folder of Markdown, text and PDF files read as the knowledge base. Read it with the Read the folder now button on the Knowledge page or with `python -m deskfern.admin sync-knowledge`. A file that changed is updated, a new one is added, an unchanged one is left as it is, and pages you pasted by hand are left alone. A file you delete from the folder is not removed from the knowledge base (removing a page is not built; see docs/14). In Docker compose the folder is the `knowledge` volume mounted at `/data/knowledge`.
- How to get it:
  1. Put your files in a folder on the server.
  2. Set the path, then read the folder.
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
- If it is wrong: Sign in answers "too many tries" sooner than expected. Three limits apply: one address, one address and one account (this burst), and one account from anywhere (failed tries only, a burst of 20 or four times this number, one more every 30 seconds). An address that signed in as that account in the last 24 hours is never held up by the account wide limit, so someone who only knows the owner's email cannot keep the owner out. A first sign in from a new address can wait while an attack on the account runs.

## CRAWL_MAX_PAGES, CRAWL_MAX_DEPTH, CRAWL_MAX_BYTES, CRAWL_DELAY_S, CRAWL_DEADLINE_S, CRAWL_TOTAL_BYTES

- Required: optional
- What it does: Limits for the site crawl. CRAWL_MAX_BYTES is the most one page may have, CRAWL_TOTAL_BYTES (default 10000000) the most the whole crawl may read, CRAWL_DEADLINE_S (default 120) the most seconds the whole crawl may take, and CRAWL_DELAY_S (0 to 10 seconds) the pause between two pages. A site's own Crawl-delay can lengthen the pause, never beyond 10 seconds. One page may take at most 30 seconds to arrive. A real site is crawled by the worker (`make worker`), so the worker must be running; only the offline sample site is read on the spot.
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

## IMAP_HOST, IMAP_PORT, IMAP_USE_SSL, SMTP_HOST, SMTP_PORT, SMTP_USE_TLS, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM

- Required: required for email intake
- What it does: Mailbox access for incoming support email and the sender address. One account (SMTP_USER and SMTP_PASSWORD) signs in for both reading and sending. Both connections check the server's certificate and name. `IMAP_USE_SSL=false` and `SMTP_USE_TLS=false` are accepted only for a mail server on this same computer (127.0.0.1). Mail is read without marking it read, and is marked read only after it is safely stored. EMAIL_FROM is the address replies leave from and must be changed from the example. Replies to tickets that came in by email leave through EMAIL_ADAPTER=smtp.
- How to get it:
  1. Gmail: turn on two step sign in, then create an app password in your Google account security page (the exact Google page name was not re-checked, UNVERIFIED).
  2. Use the mail host's IMAP and SMTP names and ports.
  3. Use a dedicated support mailbox.
- Cost: Usually part of the mail plan.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `The inbox rejected the login`.

## COMPANY_NAME, CURRENCY, APP_NAME

- Required: COMPANY_NAME is required for a live start
- What it does: COMPANY_NAME is the company the desk speaks for, used in replies. CURRENCY is the three letter code printed on refund requests (amounts always come from the payment system in its smallest unit, the desk never converts). APP_NAME is the name in the page title and in log lines.
- How to get it:
  1. Set COMPANY_NAME to your own company. The example `Fieldnote Software` is a placeholder: `python3 tools/doctor.py` lists it as a PROBLEM and a live start is refused while it is unchanged.
  2. Set CURRENCY to the code your payment system uses.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: Replies would sign off with the wrong company. The doctor names the placeholder.

## ALLOW_DEMO_SERVER, ALLOW_PRIVATE_BASE_URLS, MAX_BODY_BYTES, SEED_DIR

- Required: optional
- What it does: ALLOW_DEMO_SERVER lets a throw away demo run on a server database. Leave it `false`: a demo on a real database would load fictional data into it. ALLOW_PRIVATE_BASE_URLS lets an integration (a self hosted Chatwoot) sit on a private or loopback address; hosted helpdesks never need it, and every other adapter address must be a public https address. MAX_BODY_BYTES is the largest request the API reads (default 26214400, 25 MB); a bigger one is refused before it is read. SEED_DIR is the folder with the demo data and the recorded model answers, found automatically when you run from the repository.
- How to get it: Leave all four alone unless a step in these docs tells you to change one.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: An address on a private network is refused with a message that names the setting.

## REQUIRE_VERIFIED_SENDER, INBOUND_AUTHSERV_ID, TRUSTED_SENDER_CHANNELS

- Required: INBOUND_AUTHSERV_ID is required for the desk to treat any email sender as verified
- What it does: A sender can type any address into a message. A message whose sender could not be verified is never matched to a customer, so no account, plan or payment facts are used for it; it gets a reply that asks the person to confirm their address, and a person must confirm before any private detail is used. `REQUIRE_VERIFIED_SENDER` (default `true`) keeps that on. `INBOUND_AUTHSERV_ID` is the name your mail provider puts first in its `Authentication-Results` header (for example `mx.google.com`): the desk trusts only the top header, only when it starts with that name, and only when it says `dmarc=pass`. `TRUSTED_SENDER_CHANNELS` lists helpdesk channels (`chatwoot`, `intercom`, `zendesk`, `freshdesk`, `email`) whose own sign in already proves the address, comma separated.
- How to get it:
  1. Open one real message in your mail provider with "show original" and read the first `Authentication-Results:` line. Copy the first word after the colon.
  2. Put that word in INBOUND_AUTHSERV_ID. Leave TRUSTED_SENDER_CHANNELS empty unless your helpdesk verifies customers.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: Every email sender is treated as unverified and gets the confirmation reply. That is safe, only slower. See docs/13.

## LLM_MODE, ANTHROPIC_BASE_URL, ANTHROPIC_VERSION, LLM_HARD_MODEL, LLM_BULK_MODEL, LLM_MODEL

- Required: optional
- What it does: Used only when LLM_BACKEND is `anthropic` or `fixture`. LLM_MODE is the older switch (`live` means `anthropic` when LLM_BACKEND is empty); leave it. ANTHROPIC_BASE_URL and ANTHROPIC_VERSION name the service address and its API version. LLM_HARD_MODEL answers and judges, LLM_BULK_MODEL does the cheap jobs, and LLM_MODEL (one id for both) overrides the two. Not run against the real service (UNVERIFIED).
- How to get it: Leave the defaults. Change a model id only to one your own account can use.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: A refused model name comes back as an adapter error and the desk falls back to the offline stand in.

## LLM_TIMEOUT, LLM_MAX_TOKENS, LLM_MAX_RETRIES, LLM_CALL_BUDGET, LLM_BUDGET_USD, LLM_THINKING

- Required: optional
- What it does: LLM_TIMEOUT is seconds to wait for one answer, LLM_MAX_TOKENS the longest answer, LLM_MAX_RETRIES how many times a failed call is tried again. LLM_CALL_BUDGET is the most live model calls one process makes and LLM_BUDGET_USD the most tracked spend in US dollars; past either, calls stop with a budget message instead of spending more. `.env.example` sets 200 calls and 5 dollars so a loop can never run up a bill; an empty value means no limit, so set a limit on purpose. LLM_THINKING `off` asks for the lowest thinking setting where the model allows it.
- How to get it: Keep the example numbers, raise them when you have measured what a day of tickets costs.
- Cost: Only calls to a real model cost anything.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host does not need them.
- Check it: `python3 tools/doctor.py` prints the limits.
- If it is wrong: At the limit the desk answers with the offline stand in or hands the ticket to a person.

## LLM_RECORD, LLM_REDACT, LLM_LOG_PROMPTS

- Required: optional
- What it does: LLM_RECORD saves every live answer under LLM_FIXTURES_DIR so a later run can replay it for free. LLM_REDACT (default `true`) keeps full prompts and keys out of logs and saved answers. LLM_LOG_PROMPTS (default `false`) logs a shortened, redacted prompt. Customer text can end up in a saved answer, so keep the folder private.
- How to get it: Leave the defaults.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: Turning LLM_REDACT off puts customer text in logs.

## INTERCOM_BASE_URL, ZENDESK_BASE_URL, FRESHDESK_BASE_URL, STRIPE_BASE_URL, DRIVE_BASE_URL, NOTION_BASE_URL

- Required: optional
- What it does: The address of each service, so an offline practice server can stand in for it. Leave them at the defaults for the real service. An address must be https and public unless ALLOW_PRIVATE_BASE_URLS is on, and the tools never follow a redirect to another host.
- How to get it: Leave them empty or at the defaults in `.env.example`.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: see the paragraph at the top. The web host only needs API_BASE_URL and SESSION_SECRET.
- Check it: `python3 tools/doctor.py` (add `--live` to test the real connection).
- If it is wrong: `That address is not allowed` and the name of the setting.

## POSTGRES_PASSWORD

- Required: required for Docker compose
- What it does: The password of the database that compose starts. It is not read by the app itself; compose puts it into DATABASE_URL.
- How to get it:
  1. Run `openssl rand -hex 24` and paste the result. Letters and digits only, so it is safe inside a URL.
- Cost: Free.
- Local, Vercel, Railway, Hetzner: Hetzner only, in the `.env` next to `docker-compose.yml`.
- Check it: `docker compose config` refuses to start when it is empty.
- If it is wrong: The database container and the API cannot sign in to each other.

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
