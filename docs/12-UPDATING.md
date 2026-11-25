# Updating

1. Back up the database and the knowledge folder (`11`).
2. Read `CHANGELOG.md`.
3. `git pull`.
4. Reinstall: `make install`, or `.venv/bin/pip install -c constraints.txt -e packages/core-py -e "apps/api[dev]"` (with a uv environment: `uv pip install --python .venv/bin/python -c constraints.txt -e packages/core-py -e "apps/api[dev]"`) then `pnpm install && pnpm build`.
5. Migrate (Postgres): `cd apps/api && ../../.venv/bin/alembic upgrade head`. It reads the same settings file as the app (`ENV_FILE` if you use one, otherwise `.env`). Docker compose runs this for you on start. SQLite creates missing tables on start but does not change old ones. Across a schema change use Postgres, or export and start fresh.
6. Restart the API, worker and web app.
7. Run `python tools/doctor.py` and open the Audit log.

The shared code in `packages/` (the base) is versioned separately. Do not edit it in a client install. `.env.example` lists every setting, compare it with your `.env` after an update: `python3 tools/check_env.py` does it and names the settings your file is missing.

## Pinned packages

`constraints.txt` pins every third party Python package to the version the tests ran against, so an install today gives the same set as the one that was tested (`make install`, the Docker image and CI all use it). After a deliberate upgrade of a package, install the new version, run the tests, then rewrite the file with `tools/make_constraints.py` from the hub repository (`sendora-ai-systems`).
