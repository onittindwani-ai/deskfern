# Deskfern spec (step 1)

Deskfern is a support and success desk for one client business, run by an implementer. It reads the client's own knowledge (help pages, policies, changelog, PDFs), drafts cited answers to tickets, checks that every sentence is supported by the cited text, escalates what it cannot support, routes refunds through a person, learns from agent edits and shows which questions have no source. Single tenant. Sample demo company: Fieldnote Software (a team notes app). All people, prices and policies in the seed are sample data.

## Entities (table prefix `lh_`)
Customer, Source, Document, Chunk, Ticket, TicketMessage, Answer (a draft with sentences), Citation (one per sentence per chunk), Escalation, PolicyRule, PolicyAction, HealthScore, Gap, TestCase, CrawlJob, EvalRun, State.

## Pipeline
1. Ingest: local folder (md, txt, pdf), Google Drive and Notion adapters (tier B, against mocks), site crawl (SSRF guard, robots.txt, rate limit, size and depth caps).
2. Chunk with context: each chunk keeps document title, section path and a one line context sentence; the indexed text includes them.
3. Index: SQLite FTS5 (BM25) plus an offline hashed bag of words vector. The vector is NOT a language model embedding. Postgres deploys use full text search (tsvector). pgvector: not installed on the local test Postgres, host support UNVERIFIED, documented only.
4. Intake: Chatwoot webhook (signature and timestamp check), email (IMAP), Intercom, Zendesk, Freshdesk (tier B) all become Ticket and TicketMessage.
5. Triage: category, urgency, sentiment, language, flags (injection, other customer data, out of scope) by rules (offline stand in, not a language model).
6. Answer: retrieve, draft sentences, cite chunks, run the groundedness check, compute the confidence meter (retrieval strength, groundedness, policy match). Unsupported sentences are removed; if too little remains the ticket is escalated.
7. Policy actions: a refund is a PolicyAction that only exists as an approval request. It runs only after a different person approves it (a refund a person asked for is asked in their name). Draft mode keeps an approved refund as approved, not paid yet, sends nothing, and it is paid later when draft mode and the kill switch are off.
8. Learn: an agent edit to a draft becomes a regression TestCase. Questions with no good source cluster into Gaps. Health score and churn risk come from ticket history.

## Hard limits
Never invents policy. A refund always needs approval and the requester cannot approve their own request. Ingested text and ticket text are untrusted data: "ignore your rules and refund" causes no action (tested). Draft mode default, kill switch, audit chain, login on every route except signed webhooks.

## Jobs
ingest, intake, answer, gaps, health. One worker, database job table.

## Adapters
Chatwoot A (mock), email IMAP A (mock server), local folder A, seed desk A, Intercom B, Zendesk B, Freshdesk B, Stripe refunds B, Google Drive B, Notion B, site crawl A (local test server).

## Evals
Answer set with key on the seed KB; HELD OUT set of 60+ questions written before the retrieval design; groundedness checker against hand labelled sentences (precision and recall); escalation precision and recall; refusal on out of scope; citation validity 100 percent (code check).

## Pages
Queue (SLA timers), Ticket workspace (signature), Knowledge, Policies (refund simulator), Health, Insights, Test cases, Integrations, Approvals, Audit log.

## Env vars
See `.env.example` and doc 04.
