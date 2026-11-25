# Architecture

```
Help desk / email / webhook  ->  ticket intake  ->  triage  ->  search (word match + similarity, merged)
                                                                 |
Knowledge sources -> ingest -> chunks (title, section, context)  v
                                              answer writer -> groundedness check -> confidence meter
                                                                 |
                              policies (refund rules) -> approval -> payment adapter (draft mode blocks)
```

- **API:** FastAPI built on the shared base (`create_base_app`), SQLAlchemy 2, Alembic. SQLite for the demo, Postgres for real use.
- **Jobs:** a table in the database, one worker process. No separate queue service. The worker brings in tickets, refreshes insights, pays approved refunds that draft mode or the kill switch held back, and runs the site crawls that the Knowledge page queues.
- **Search:** SQLite FTS5 (BM25) plus a hashed word similarity vector, merged by reciprocal rank fusion. On Postgres a full text query is used instead (run once against a throwaway local Postgres 17; the main tests use SQLite). pgvector is not used.
- **Answers:** by default an offline writer picks sentences. A language model writer sits behind the same interface (`model_tasks.py`, `LLM_BACKEND=anthropic`), is tested against a local fake server and recordings, and has never run with a real key (UNVERIFIED). The groundedness check runs on its sentences either way.
- **Groundedness:** seven checks per sentence, threshold 0.65 (see `ANSWER-EVAL.md`).
- **Web:** Next.js 16 on the shared `@sendora/ui-kit`. It forwards to the API and never holds vendor keys.
- **Adapters:** one interface per category in `apps/api/src/deskfern/adapters`. The Integrations page lists them.
- **Tables:** <!--n:tables-->18<!--/n--> tables, all prefixed `lh_`.
