# Operating and backups

## Daily

- Open the Queue and clear what is past its reply target.
- Open Approvals and decide waiting refunds. The person who asked cannot approve. Under Approved, not paid yet are refunds approved while draft mode or Pause all was on; they are paid by the worker once both are off, or press Pay approved refunds now.
- Skim the Audit log for anything unexpected.

## Weekly

- Knowledge, Gaps: write the missing pages. Check the pages that disagree.
- Test cases: run them all after any page or policy change.
- Audit log: press Check the chain. If it says broken, stop and look.

## Stopping fast

Press **Pause all**. It blocks every reply and refund at once. A reply sent while it is on is held (the ticket stays open and its timer keeps running), and an approved refund waits. To stop even when the web app is down, set `KILL_SWITCH=true` and restart the API and worker.

## Backups

- **SQLite:** `sqlite3 deskfern.sqlite3 ".backup backup.sqlite3"` while it runs, or stop the app and copy the file.
- **Postgres:** `pg_dump --format=custom "$DATABASE_URL_PLAIN" > deskfern.dump` using the plain `postgresql://` form of the address. `deploy/hetzner/backup.sh` does it for you.
- **Knowledge files** live in `KNOWLEDGE_DIR`. Back that folder up as well.

## Restore (test this once)

1. Make an empty database. 2. Postgres: `pg_restore --clean --if-exists -d "$DATABASE_URL_PLAIN" deskfern.dump`. SQLite: put the file back. 3. Start the API, open the Audit log and press Check the chain.

## Logs

The API and worker log to the console (`docker compose logs -f api worker`). Logs can hold customer names and ticket text. Check them before you share them.
