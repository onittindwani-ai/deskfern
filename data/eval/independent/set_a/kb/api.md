# API
Last updated: 2026-01-12

## Rate limits
Each API key can make 120 requests per minute. This was raised from 60 requests per minute on 2026-01-12. Requests over the limit receive HTTP 429 with a Retry-After header.

## Webhooks
Failed webhook deliveries are retried 5 times over 24 hours. After that the webhook is marked failed and shown in the dashboard.

## Keys
An API key is shown only once, when you create it. If you lose it, create a new key and delete the old one. Each account can have up to 10 active API keys.
