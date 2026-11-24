# Security

## Built in

- Refunds: the desk only asks. A different person approves. Execution is idempotent and blocked in draft mode and by the kill switch.
- Customer text is data. Instruction like text in a ticket is flagged and never followed.
- Another customer's data is never shown. Such tickets go to a person.
- Website crawling: public addresses only, resolved address checked before each request and the peer address after connecting where reported, redirects re-checked, robots.txt respected, rate limit and caps on pages, depth and size.
- Webhooks: the Chatwoot signature (HMAC SHA 256 over timestamp and body, five minute age limit) is checked before any work.
- Login: password hashes only, rate limited, cookie flags set, a required header on every change so another site cannot make the browser change data.
- Audit log with a hash chain.
- Secrets are read from the environment and never printed. `tools/scan_secrets.py` runs in CI.

## Checked

- `pip-audit` and `pnpm audit` found no known vulnerabilities on 2026-11-23. Secret scan clean. Tests cover sign in rate limiting, self approval refusal, webhook signatures, the crawler guard, damaged PDFs and links out of a knowledge folder, and the required request header on the web proxy.

## Not tested or not done

- No penetration test. No real vendor was contacted.
- The peer address check after connecting depends on what the HTTP library reports.
- Docker images were not built and the deploy files were not run.
- Rate limits are per process. Behind several processes they are not shared.
- The Drive token is not refreshed automatically.
- If you connect a language model, ticket text and cited passages go to that vendor. Choose and review accordingly.

## Report a problem

Open a private security advisory on the repository. Do not post keys or customer data in an issue.
