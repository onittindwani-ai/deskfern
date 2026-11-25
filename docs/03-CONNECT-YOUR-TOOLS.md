# Connect your tools

Every connection is optional and off until you fill its variables (see `04-ENVIRONMENT-VARIABLES.md`). The **Integrations** page shows which are on, with a Test button for each. Nothing here was tried against a real vendor account: each adapter was tested against a local copy that imitates the vendor, so the first real test is yours, in draft mode.

| Need | Options in this repo | Notes |
| --- | --- | --- |
| Help desk | Chatwoot, Intercom, Zendesk, Freshdesk | Chatwoot webhook signing was read from the Chatwoot help article. The others were not read from vendor pages. |
| Email intake | Any IMAP and SMTP mailbox | Use a dedicated support mailbox. Set `INBOUND_AUTHSERV_ID` so the desk can tell a real sender from a forged one (`04`, `13`). Replies to email tickets leave through `EMAIL_ADAPTER=smtp`. |
| Knowledge sources | Folder, Google Drive, Notion, website crawl | Drive and Notion tokens expire or need sharing steps, see doc 04. |
| Refunds | Stripe refunds | Needs a restricted key that can write refunds only. |

Scored comparisons with alternatives and their prices are in `TOOLS-AND-ALTERNATIVES.md`. Prices were not read from vendor pages, so check them.

## The safe order

1. Leave `MODE=draft`. In draft mode the desk reads and drafts, and sends nothing.
2. Connect the help desk, run **Test**, import a few real tickets.
3. Connect a knowledge source. For a folder, set `KNOWLEDGE_DIR` and press **Read the folder now** on the Knowledge page (or run `python -m deskfern.admin sync-knowledge`). Open the Knowledge page and read the gaps list. Import your customers too, so refund answers can read their plan and payments (`10`).
4. Run `python3 tools/doctor.py --live` to test each connection.
5. Only then follow `09-GO-LIVE-CHECKLIST.md`.
