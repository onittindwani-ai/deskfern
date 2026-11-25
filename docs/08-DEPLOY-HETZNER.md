# Deploy on a Hetzner server

**Not tested on a real server: no Hetzner server was created for this project.** The repository's CI workflow (`.github/workflows/ci.yml`) builds the Docker images and starts the whole stack on a throwaway GitHub machine, and checks that the API and the web app answer. That proves the images build and start, not that your server, your DNS names or your certificates work. The server steps below are from our general knowledge: check them against https://docs.hetzner.com. One small server runs everything with Docker Compose and Caddy, which gets an HTTPS certificate for you.

1. Make a server (Ubuntu LTS is fine). Add your SSH key while creating it.
2. Add a firewall that allows only ports 22, 80 and 443.
3. Point two DNS names at the server: one for the web app and one for the API.
4. Optional: paste `deploy/hetzner/cloud-init.yaml` into the cloud config box. It installs Docker and Caddy and holds no secrets. Do not add any.
5. Log in with SSH and clone your repository to `/opt/deskfern`.
6. Make the settings file: `cp .env.example .env`, then fill in exactly these (the rest is explained in `04`):
   - `DEMO_MODE=false` (it already is in the example).
   - `SESSION_SECRET`: 32 or more random characters. `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. The app refuses to start on the placeholder.
   - `OWNER_EMAIL` and `OWNER_PASSWORD_HASH`: your own address and the hash from `python3 tools/hash_password.py`, kept inside single quotes.
   - `POSTGRES_PASSWORD`: `openssl rand -hex 24` (letters and digits only, so it is safe inside the database address).
   - `COMPANY_NAME`, `CURRENCY` and `EMAIL_FROM` for your own company.
7. Put your two DNS names in `deploy/hetzner/Caddyfile`.
8. `docker compose up -d --build`. The API runs the database migrations itself on start.
9. Copy the Caddyfile to `/etc/caddy/Caddyfile` and `sudo systemctl reload caddy`.
10. Open the web address and sign in with the owner email and password.
11. Put your help pages in: copy files into the `knowledge` volume (`docker compose cp ./help-pages api:/data/knowledge/`), then press **Read the folder now** on the Knowledge page. Or paste pages by hand on the same page.
12. Put your customers in (their plan and payment facts, which refund answers read): `docker compose cp customers.csv api:/tmp/customers.csv` and `docker compose exec api python -m deskfern.admin import-customers /tmp/customers.csv`. The columns are in `10`.
13. Connect your helpdesk and mailbox (`03`), then follow the go live checklist (`09`). The desk stays in draft mode, sending nothing, until you do.

## What the API host serves

The Caddyfile publishes the API host for two paths only: `/api/webhooks/chatwoot` (so Chatwoot can post new messages) and `/health` (for an uptime check). Every other address on that host answers 404. The sign in and every other API call go through the app host, whose web server passes them on over the private compose network and puts the visitor's real address in the request. Compose sets `FORWARDED_ALLOW_IPS` to the private network ranges so the API trusts that address only from the web container, never from the internet. If you do not use Chatwoot, you can delete the whole API block and no API host is needed.

## Backups

`deploy/hetzner/backup.sh` writes a compressed Postgres dump and a copy of the knowledge volume to `/var/backups/deskfern` and keeps the newest 14 of each. It writes to a partial file first and stops with an error when the dump is empty, so a failed run never pushes a good backup out of the rotation. Run it nightly with cron, from any folder: `0 3 * * * /opt/deskfern/deploy/hetzner/backup.sh`. A copy on the same disk is not a backup: set `BACKUP_COPY_TO` to a folder on another machine and the script copies there too. Test a restore (see `11`) before you need it.
