# Deploy on Railway

**Not tested: no Railway account was used.** Every step is UNVERIFIED and comes from our general knowledge of Railway. Check each against https://docs.railway.com before you rely on it. A starting `railway.json` is in `deploy/railway/` in case your account accepts it.

You make four things in one Railway project: a Postgres database, the API, the worker and, if you do not use Vercel, the web app.

1. New project, then add Postgres.
2. Add a service from your Git repository named `api`. Dockerfile path `apps/api/Dockerfile`, repository root as the build context. Health check path `/health`.
3. Variables for `api`: `DATABASE_URL` (use the Postgres reference and make it start with `postgresql+psycopg://`), `SESSION_SECRET`, `MODE=draft`, `DEMO_MODE=false`, `OWNER_EMAIL`, `OWNER_PASSWORD_HASH` and your adapter choices (see `04`).
4. The Dockerfile start command runs `alembic upgrade head` on boot for Postgres.
5. Add a service `worker` with the same Dockerfile and variables and start command `python -m deskfern.worker`. No public address.
6. Knowledge files are read from `KNOWLEDGE_DIR`. Attach a volume to `api` and `worker`, point `KNOWLEDGE_DIR` at it, or synced files vanish on redeploy.
7. Add a service `web` (Dockerfile path `apps/web/Dockerfile`) with `API_BASE_URL` set to the API's private address, or use Vercel.
8. Give `web` a public domain. Keep `api` private if you can.
9. Open the `api` shell and run `python tools/doctor.py`.

Keep `MODE=draft` until `09-GO-LIVE-CHECKLIST.md` is finished.
