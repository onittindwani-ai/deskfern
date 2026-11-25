# Go live checklist

Do every line. Do not skip the first run in draft mode.

1. **Facts to confirm with the business** (the shipped defaults are a decided starting point: refunds within 7 days for monthly plans and 14 days for annual plans, one per customer per 12 months, duplicate charges always refunded, every refund approved by a person; first reply in 1, 4, 24 and 72 hours by priority, counted in business hours; plain, friendly, short replies). Confirm each one with the business and change what differs: refund rules and limits, first reply targets and whether they count business hours, tone of voice, who handles escalations, and what must never be answered by the desk.
2. `DEMO_MODE=false`, a real `OWNER_PASSWORD_HASH`, a long `SESSION_SECRET` (32 or more characters), the second approver login set (`APPROVER_EMAIL`, `APPROVER_PASSWORD_HASH`; with `MODE=live` and a real payment system the app refuses to start without it), and your own `COMPANY_NAME`, `OWNER_EMAIL` and `EMAIL_FROM`. A live start is refused while any of those three is still the example, and while no reply could leave (set `HELPDESK_ADAPTER` or `EMAIL_ADAPTER=smtp`). If email comes in, set `INBOUND_AUTHSERV_ID` (see `04`), or every email sender will be treated as unverified.
3. Load the real knowledge base (a folder in `KNOWLEDGE_DIR` and **Read the folder now**, or pasted pages) and your customers (`10`, the customer file). Read the Gaps and the pages-that-disagree lists, and fix the pages.
4. Run in draft mode for at least a week. Compare the drafts with what your agents actually sent. Add the differences as test cases.
5. Run the held out questions on your own pages: write 60 or more real questions with the right page for each, and run `tools/eval_heldout.py` on them. Do not go live with answers you did not measure. The demo numbers in `ANSWER-EVAL.md` are for a made up company only.
6. Test the kill switch: press Pause all, confirm a draft cannot be sent, then release it.
7. Test one refund end to end in your payment provider's test mode, with a second person approving.
8. Set `MODE=live` on the server only. Start with one channel and a small share of tickets, and keep a person reading every reply that mentions money for the first weeks.
9. Back up the database (doc 11) and test the restore once.
10. Decide who reads the Insights page weekly.

When anything looks wrong: Pause all first, ask questions after.
