# Chatwoot

Checked 2026-11-23.

- VERIFIED from the vendor page: the create message endpoint `POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages`, the body fields (`content` required, `message_type`, `private` and others) and that `Authorization: Bearer <token>` is the current header while `api_access_token` is called legacy. The adapter now sends both.
- Webhook signature: read earlier from the Chatwoot help article (sha256 HMAC over timestamp and raw body).
- No machine readable spec was fetched (the fetch tool returns page text). The page has no example response body.
- Still UNVERIFIED: the real response of a created message, rate limits, and the webhook payload field names the parser reads.

Contract test: `mock/contracts/chatwoot/` and `mock/tests/test_contracts.py`. It proves the adapter keeps to the saved field list, not that Chatwoot agrees.
