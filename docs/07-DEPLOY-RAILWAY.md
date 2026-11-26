# Deploy on Railway

**Not tested: no Railway account was used.** Every step is UNVERIFIED and comes from our general knowledge of Railway. Check each against https://docs.railway.com before you rely on it. A starting `railway.json` is in `deploy/railway/` in case your account accepts it.

You make four things in one Railway project: a Postgres database, the API, the worker and, if you do not use Vercel, the web app.

1. New project, then add Postgres.
2. Add a service from your Git repository named `api`. Dockerfile path `apps/api/Dockerfile`, repository root as the build context. Health check path `/health`.
3. Variables for `api`: `DATABASE_URL` (use the Postgres reference and make it start with `postgresql+psycopg://`), `SESSION_SECRET` (32 or more random characters), `MODE=draft`, `DEMO_MODE=false`, `OWNER_EMAIL`, `OWNER_PASSWORD_HASH`, `COMPANY_NAME`, `EMAIL_FROM` and your adapter choices (see `04`). Put the hash inside single quotes.
4. The Dockerfile start command runs `alembic upgrade head` on boot for Postgres. Also set `FORWARDED_ALLOW_IPS` on `api` to the private network range the `web` service reaches it from (UNVERIFIED: look at the address the API logs for a request from `web`; Railway's private network uses its own range, so check the current Railway docs). The image only trusts `127.0.0.1` by default, so without it every visitor looks like one address and the sign in limits are shared by everyone: one bad try from a stranger every 10 seconds can then keep the owner from signing in from a new browser. A browser that already signed in carries a signed note and is not held up. The settings page warns when the API sees a visitor address it does not trust.
5. Add a service `worker` with the same Dockerfile and variables and start command `python -m deskfern.worker`. No public address.
6. Knowledge files are read from `KNOWLEDGE_DIR`. Attach a volume to `api` and `worker`, point `KNOWLEDGE_DIR` at it, or synced files vanish on redeploy.
7. Add a service `web` (Dockerfile path `apps/web/Dockerfile`) with `API_BASE_URL` set to the API's private address, or use Vercel.
8. Give `web` a public domain. Keep `api` private: the web service reaches it over the private network, and the sign in and every API call go through `web`. Give `api` a public address only if Chatwoot must post to it, and then put something in front that lets through only `/api/webhooks/chatwoot` and `/health` (the Hetzner Caddyfile in `deploy/hetzner/` shows the rule). Every other API address needs a signed in session, but a smaller public surface is safer.
9. Open the `api` shell and run `python tools/doctor.py`. Import your customers and read your help pages from the same shell (`python -m deskfern.admin import-customers` and `sync-knowledge`, see `10`).

Keep `MODE=draft` until `09-GO-LIVE-CHECKLIST.md` is finished.
