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

- **Retention.** Once a day the worker removes the words of finished tickets (replied, refused, duplicate, closed) older than `RETENTION_DAYS` (default 730; 0 keeps everything). Say the same number in your privacy notice. A ticket with a refund record stays as an empty shell that keeps its link to the customer, so the refund rule (one per 12 months) still counts it. Under 365 days the settings page warns, because the words are then gone before the refund look back is over.
- **Erase one customer on request.** As the owner, `python -m deskfern.admin erase-customer person@example.com` (or `POST /api/customers/erase`). It stops while a refund for that person is still waiting or being paid. It cannot be undone. The address stays only as a salted hash, so importing the customer file again does not bring them back, and a new message from that address is not stored with its words: it becomes a small ticket that says only that a message from an erased address arrived and where to find it (it holds no address, subject or text, is flagged `from an erased address`, and counts in the settings problems and on the Queue). One more thing is kept: each refund record of an erased customer carries a salted hash of the address (`customer_hash`, no name or address, cannot be read back), so that if you later lift the erasure and import the person again, the 12 month refund rule still counts that refund. Pages you pasted or crawled that quote a customer are yours to edit on the Knowledge page.
- **Accept an erased address again.** The owner runs `python -m deskfern.admin lift-erasure person@example.com` (or `POST /api/customers/lift-erasure`). Only the suppression is removed and the action is audited without the address. Nothing that was erased comes back.
- **Messages the desk could not take in.** A message that is too large and has no readable text part, one that cannot be read or stored, and one from an erased address each become a small ticket "with a person" (the first reply clock runs), with a note saying why and that the original is still in the mailbox or helpdesk. The Queue and the settings page list what intake noticed (a helpdesk that answered with something unreadable, a gap in a helpdesk list, skipped mail) under Needs attention; a source's own warning clears when it works again and one off notes disappear after 7 days.
- **Backups still hold the data.** A dump made before the erase or the purge keeps the words until it is rotated out (`deploy/hetzner/backup.sh` keeps the newest 14). If you restore an old dump, run the erase again for everyone who asked in the meantime. The backup files are written readable by their owner only.
- **A ticket handed to a person** can be mailed to `ESCALATION_NOTIFY_EMAIL`, and the bar at the top of every page counts the hand overs waiting.

## Restore (test this once)

1. Make an empty database. 2. Postgres: `pg_restore --clean --if-exists -d "$DATABASE_URL_PLAIN" deskfern.dump`. SQLite: put the file back. 3. Start the API, open the Audit log and press Check the chain.

## Logs

The API and worker log to the console (`docker compose logs -f api worker`). Logs can hold customer names and ticket text. Check them before you share them.
