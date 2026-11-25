# Security

## Built in

- Refunds: the desk only asks. A different person approves a refund that a person asked for (by hand, or by pressing Draft again or Confirm sender): it is asked in that person's name and the base refuses their own decision. A refund the worker asks for by itself is asked in Deskfern's name, so the owner or the approver may decide it. With `MODE=live` and a real payment system the app refuses to start without a separate approver login (`APPROVER_EMAIL` and its hash). Execution is claimed with one conditional update and carries a payment idempotency key, so it never pays twice, and it is blocked in draft mode and by the kill switch as they are set in the app. A refund approved while one of them was on is kept as approved, not paid yet; the worker (every minute) and the Pay approved refunds button on the Approvals page pay it once both are off, and a second ticket about that payment says where it stands.
- Customer text is data. Instruction like text in a ticket is flagged and never followed.
- Another customer's data is never shown. Such tickets go to a person.
- Website crawling: public addresses only, resolved address checked before each request and the peer address after connecting where reported, redirects re-checked, robots.txt respected, rate limit and caps on pages, depth and size. A site's own Crawl-delay is capped at 10 seconds, one page may take at most 30 seconds to arrive, the whole crawl has a time limit (`CRAWL_DEADLINE_S`) and a size limit (`CRAWL_TOTAL_BYTES`), and a real site is crawled by the worker, never inside a web request.
- Webhooks: the Chatwoot signature (HMAC SHA 256 over timestamp and body, five minute age limit) is checked before any work. The body is read in small pieces and refused as soon as it passes `MAX_BODY_BYTES`, so a huge or endless upload is never held in memory.
- Who a message is from: a sender address in a message is just text anyone can type. A message whose sender could not be verified (email: the mail provider you name must have written a passing DMARC result in the top `Authentication-Results` header; a helpdesk channel only when you list it as trusted) is never matched to a customer, so no plan, payment or account fact is used for it, and the reply asks the person to confirm. See `docs/13-SECURITY-AND-DATA.md`.
- Email: certificates and host names are checked on both the inbox and the sender connection. Plain text is accepted only for a mail server on the same computer. Mail is read without marking it read and marked read only after it is stored, so a failed save loses nothing. A reply to a ticket that came by email is sent by the email adapter and the ticket says whether it really left.
- A message that cannot be stored (broken data, an enormous Message-ID, characters a database refuses) is skipped on its own and the rest of the batch goes on. Customer text is not written into the logs. One run takes at most 50 mailbox messages and never downloads one over 2 MB (it stays unread for a person); a mail with only an HTML part is read as text.
- Replies: the answer is claimed and the claim committed before anything leaves, so a timeout or a crash can never lead to a blind second copy. A reply held back by draft mode or Pause all leaves the ticket open and its reply clock running. A send that may or may not have been delivered (a timeout after the request went out, a dropped connection, a server error) waits for a person to say whether the customer got it; nothing is sent again until then. A text that makes ticket checks slow is bounded: scans are cut to 20,000 characters after long runs of one character are squeezed, and tested against hostile strings under a time limit.
- Login: password hashes only, sign in is limited per address, per address and account, and per account from anywhere (the account wide limit counts failed tries only, and never holds up an address that signed in as that account in the last 24 hours, so someone who only knows the owner's email cannot lock the owner out), the address is compared without spaces or capitals, an unknown address costs the same work as a wrong password, cookie flags set, a required header on every change so another site cannot make the browser change data. The public demo logins and the demo buttons switch off by themselves the moment `OWNER_PASSWORD_HASH` is set, even if `DEMO_MODE` stays on. The app and the worker refuse to start on an example or short `SESSION_SECRET` whenever DEMO_MODE is off, an owner password is set, or MODE is live (a demo flag left on cannot make the example secret acceptable: with it anyone could forge an owner session token). A live start is also refused while the company name, owner address or sender address is still an example.
- Web: the proxy to the API takes the visitor address from the last hop only (a caller cannot invent it), refuses oversize bodies, and only passes on paths made of letters, digits and `. _ ~ -` (a query mark, semicolon, percent sign or backslash inside a path segment is refused, so the sign in route cannot be reached in disguise).
- Audit log with a hash chain.
- Secrets are read from the environment and never printed. `tools/scan_secrets.py` runs in CI.

## Checked

- `pip-audit` and `pnpm audit` found no known vulnerabilities on 2026-11-23. Secret scan clean. Tests cover sign in rate limiting, self approval refusal, webhook signatures, the crawler guard, damaged PDFs and links out of a knowledge folder, and the required request header on the web proxy.

## Not tested or not done

- No penetration test. No real vendor was contacted.
- The peer address check after connecting depends on what the HTTP library reports.
- The CI workflow builds the Docker images and starts the stack on a GitHub machine, but the deploy files were never run on a real Hetzner, Railway or Vercel account.
- Postgres only: the fixes for text a Postgres refuses (a zero byte, an over long value, a very long search word) were written from how Postgres behaves and tested on SQLite, not on a running Postgres.
- Website crawling checks the address before each request and the address connected after it, but does not pin the connection to the checked address.
- Sign in, the residual risk: while someone is failing on purpose at the owner's account, a first sign in from an address the owner has not used in the last 24 hours (a new phone, a new office) waits until the failures slow down (they refill one try every 30 seconds). Someone who shares the owner's address (an office behind one address, a mobile carrier) is held only by the per address and account limit at the owner's account, one try every 12 seconds, instead of one every 30 seconds from anywhere. The memory of known addresses is in one process and is lost on a restart, and it is only as good as the visitor address the API sees: behind a proxy that does not pass the visitor on (`FORWARDED_ALLOW_IPS`, `x-forwarded-for`), every visitor looks like one address, which also weakens the per address limits.
- Intercom, Zendesk and Freshdesk lists are read up to 5 pages per run by following each vendor's own paging pointer, written from memory of their public docs and tested only against local imitations (UNVERIFIED live). If more than 5 pages of new tickets arrive between two runs, the rest come in the next run only if the vendor lists them within those pages.
- Customer erase (a person's request to delete their data) is not built.
- Rate limits (and the sign in memory above) are per process. Behind several processes they are not shared.
- The Drive token is not refreshed automatically.
- If you connect a language model, ticket text and cited passages go to that vendor. Choose and review accordingly.

## Two risks to know before you use it

- **Demo mode is public on purpose.** With `DEMO_MODE=true` and no owner password, anyone who can reach the app can sign in with the published demo password and press the demo buttons. Never expose a demo to the internet with real data in it.
- **A secret pasted anywhere is lost.** If you paste a key into a chat, an issue, a screenshot or Git, make a new one and revoke the old one at once.

## Report a problem

Use the Security tab of the repository and choose Report a vulnerability, which opens a private advisory. We aim to acknowledge it within 5 working days. Do not post keys or customer data in an issue.
