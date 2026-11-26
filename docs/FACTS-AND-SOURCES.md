# Facts and sources

| Fact | Source | Read on | Status |
| --- | --- | --- | --- |
| Chatwoot webhook signature is sha256 HMAC over timestamp and raw body, headers carry the signature and timestamp | Chatwoot help article on webhook signing | 2026-11-23 | VERIFIED |
| Stripe creates a refund with `POST /v1/refunds` | stripe.com API reference | 2026-11-23 | VERIFIED |
| Vercel monorepo Root Directory steps | vercel.com/docs/monorepos | 2026-11-23 | VERIFIED |
| Anthropic Messages API request and structured output shape used by the base wrapper | Vendor docs read for the base on 2026-11-23, see the hub FACTS-AND-SOURCES | not re-read here | Request shape tested against a local mock server only; live use UNVERIFIED (`facts/models.md`) |
| Intercom, Zendesk, Freshdesk, Drive and Notion request shapes | Not read from vendor pages | none | UNVERIFIED, tested against local imitations only |
| Vendor prices and free tiers | Not read | none | UNVERIFIED, see `TOOLS-AND-ALTERNATIVES.md` |
| Railway and Hetzner steps | Not read | none | UNVERIFIED |

Anything marked UNVERIFIED must be checked against the vendor's current documentation before you depend on it. The seed company, customers and tickets are sample.
