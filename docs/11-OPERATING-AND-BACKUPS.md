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

## Keeping and erasing customer data

- **Retention.** Once a day the worker removes the words of finished tickets (replied, refused, duplicate, closed) older than `RETENTION_DAYS` (default 730; 0 keeps everything). Say the same number in your privacy notice.
- **Erase one customer on request.** As the owner, `python -m deskfern.admin erase-customer person@example.com` (or `POST /api/customers/erase`). It stops while a refund for that person is still waiting or being paid. It cannot be undone. The address stays only as a salted hash, so importing the customer file again or a new message from them does not bring them back. Pages you pasted or crawled that quote a customer are yours to edit on the Knowledge page.
- **Backups still hold the data.** A dump made before the erase or the purge keeps the words until it is rotated out (`deploy/hetzner/backup.sh` keeps the newest 14). If you restore an old dump, run the erase again for everyone who asked in the meantime. The backup files are written readable by their owner only.
- **A ticket handed to a person** can be mailed to `ESCALATION_NOTIFY_EMAIL`, and the bar at the top of every page counts the hand overs waiting.

## Restore (test this once)

1. Make an empty database. 2. Postgres: `pg_restore --clean --if-exists -d "$DATABASE_URL_PLAIN" deskfern.dump`. SQLite: put the file back. 3. Start the API, open the Audit log and press Check the chain.

## Logs

The API and worker log to the console (`docker compose logs -f api worker`). Logs can hold customer names and ticket text. Check them before you share them.
