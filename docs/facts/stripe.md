# Stripe refunds

Checked 2026-11-23. The adapter is tier B.

- VERIFIED from the vendor page (https://docs.stripe.com/api/refunds/create): `POST /v1/refunds`, the parameters (`charge`, `amount` in the smallest unit, `reason` of duplicate, fraudulent or requested_by_customer, `metadata`), and the example response object with `id`, `object`, `amount`, `charge`, `status`.
- Stripe publishes an OpenAPI file, but it is about 7 MB and the fetch tool could not return it, so the contract test uses the page excerpt.
- UNVERIFIED: the `Idempotency-Key` header page was not read today; error shapes; behaviour with a restricted key.
- A refund is only ever requested after a person approves it (see `13-SECURITY-AND-DATA.md`). Never run against the real service.

Contract test: `mock/contracts/stripe/` and `mock/tests/test_contracts.py`.
