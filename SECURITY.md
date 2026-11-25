# Security

## Built in

- Refunds: the desk only asks. A different person approves. Execution is idempotent and blocked in draft mode and by the kill switch.
- Customer text is data. Instruction like text in a ticket is flagged and never followed.
- Another customer's data is never shown. Such tickets go to a person.
- Website crawling: public addresses only, resolved address checked before each request and the peer address after connecting where reported, redirects re-checked, robots.txt respected, rate limit and caps on pages, depth and size.
- Webhooks: the Chatwoot signature (HMAC SHA 256 over timestamp and body, five minute age limit) is checked before any work. The body is read in small pieces and refused as soon as it passes `MAX_BODY_BYTES`, so a huge or endless upload is never held in memory.
- Who a message is from: a sender address in a message is just text anyone can type. A message whose sender could not be verified (email: the mail provider you name must have written a passing DMARC result in the top `Authentication-Results` header; a helpdesk channel only when you list it as trusted) is never matched to a customer, so no plan, payment or account fact is used for it, and the reply asks the person to confirm. See `docs/13-SECURITY-AND-DATA.md`.
- Email: certificates and host names are checked on both the inbox and the sender connection. Plain text is accepted only for a mail server on the same computer. Mail is read without marking it read and marked read only after it is stored, so a failed save loses nothing. A reply to a ticket that came by email is sent by the email adapter and the ticket says whether it really left.
- A message that cannot be stored (broken data, an enormous Message-ID, characters a database refuses) is skipped on its own and the rest of the batch goes on. Customer text is not written into the logs.
- Login: password hashes only, sign in is limited per visitor, per address and per account, the address is compared without spaces or capitals, an unknown address costs the same work as a wrong password, cookie flags set, a required header on every change so another site cannot make the browser change data. The public demo logins and the demo buttons switch off by themselves the moment `OWNER_PASSWORD_HASH` is set, even if `DEMO_MODE` stays on. The app refuses to start on an example `SESSION_SECRET` outside the demo, and a live start is refused while the company name, owner address or sender address is still an example.
- Web: the proxy to the API takes the visitor address from the last hop only (a caller cannot invent it), refuses oversize bodies, and only passes on paths it recognises.
- Audit log with a hash chain.
- Secrets are read from the environment and never printed. `tools/scan_secrets.py` runs in CI.

## Checked

- `pip-audit` and `pnpm audit` found no known vulnerabilities on 2026-11-23. Secret scan clean. Tests cover sign in rate limiting, self approval refusal, webhook signatures, the crawler guard, damaged PDFs and links out of a knowledge folder, and the required request header on the web proxy.

## Not tested or not done

- No penetration test. No real vendor was contacted.
- The peer address check after connecting depends on what the HTTP library reports.
- The CI workflow builds the Docker images and starts the stack on a GitHub machine, but the deploy files were never run on a real Hetzner, Railway or Vercel account.
- Postgres only: the fixes for text a Postgres refuses (a zero byte, an over long value, a very long search word) were written from how Postgres behaves and tested on SQLite, not on a running Postgres.
- Website crawling checks the address before each request and the address connected after it, but does not pin the connection to the checked address, and a crawl runs inside the request, not on a queue.
- Customer erase (a person's request to delete their data) is not built.
- Rate limits are per process. Behind several processes they are not shared.
- The Drive token is not refreshed automatically.
- If you connect a language model, ticket text and cited passages go to that vendor. Choose and review accordingly.

## Two risks to know before you use it

- **Demo mode is public on purpose.** With `DEMO_MODE=true` and no owner password, anyone who can reach the app can sign in with the published demo password and press the demo buttons. Never expose a demo to the internet with real data in it.
- **A secret pasted anywhere is lost.** If you paste a key into a chat, an issue, a screenshot or Git, make a new one and revoke the old one at once.

## Report a problem

Use the Security tab of the repository and choose Report a vulnerability, which opens a private advisory. We aim to acknowledge it within 5 working days. Do not post keys or customer data in an issue.
