# Updating

1. Back up the database and the uploads folder (`11`).
2. Read `CHANGELOG.md`.
3. `git pull`.
4. Reinstall: `make install`, or `.venv/bin/pip install -e packages/core-py -e "apps/api[dev]"` (with a uv environment: `uv pip install --python .venv/bin/python -e packages/core-py -e "apps/api[dev]"`) then `pnpm install && pnpm build`.
5. Migrate (Postgres): `cd apps/api && ../../.venv/bin/alembic upgrade head`. SQLite creates missing tables on start but does not change old ones. Across a schema change use Postgres, or export and start fresh.
6. Restart the API, worker and web app.
7. Run `python tools/doctor.py` and open the Audit log.

The shared code in `packages/` (the base) is versioned separately. Do not edit it in a client install. `.env.example` lists every setting, compare it with your `.env` after an update.
