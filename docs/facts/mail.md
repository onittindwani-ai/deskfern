# Mail (IMAP and SMTP)

Checked 2026-11-23. These are protocols, not a web API: SMTP (RFC 5321), message format (RFC 5322), IMAP4rev1 (RFC 3501). There is no OpenAPI or other machine readable contract. The adapters use the Python standard library clients, which implement these RFCs.

What was run: the adapters against local fake servers (`mock/mail_servers.py`), and a test that a message the sender builds has the RFC 5322 header fields. Never run against a real mail host. UNVERIFIED: provider specific rules (app passwords, rate limits).
