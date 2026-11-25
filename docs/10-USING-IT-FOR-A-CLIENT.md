# Using it for a client

One copy of Deskfern serves one client. Do not mix two clients in one database, one knowledge base or one set of logins.

## What you are selling

Setup, a trial period in draft mode, and a monthly review of the help pages, the gaps and the approvals. You are selling a desk that drafts cited answers and hands the hard ones to a person, not a promise that every answer is right and not a promise of fewer staff. Say that plainly. Deskfern never pays a refund by itself: a person approves each one.

## What to charge for

This guide gives no price figures. It gives the shape of the offer, so you can price it yourself after looking at your market and your costs.

- **Setup, once.** Business basics check (`02`), a fresh copy for the client, connecting the help centre or chat tool and the payment provider's test mode, loading the client's real help pages, writing the refund and escalation rules down with the client, setting the reply targets, and a trial period in draft mode. Charge for the hours and the care, not for the software, which is free.
- **Running it, monthly.** A short review of the pages that disagree, the gaps list, the held out check on real questions, the approvals that waited too long, and the test cases after every page or policy change. State plainly how many hours a month that includes.
- **Page writing, separately.** The desk shows which questions no page answers well. Writing those pages is real work for the client's own team or for you, priced on its own.
- **Costs that pass through, at cost.** Hosting, the help desk tool's own fees, the payment provider's fees and any model usage. Show these as the client's own accounts, not as your markup.
- **Never charge for** a promised drop in tickets, a promised score, or a promised amount of time saved. Never present the demo numbers as a result: the demo company is fictional.

## Onboarding checklist

1. Run `02-BUSINESS-BASICS-CHECK.md` with the client: sending address and DNS records, privacy notice, how long tickets are kept, a human path in every reply.
2. Make a fresh copy for this client with `DEMO_MODE=false`, their `COMPANY_NAME`, `EMAIL_FROM`, their support address in `OWNER_EMAIL`, the password hash from `tools/hash_password.py` and their own `SESSION_SECRET`. These are settings, not code: nothing in the code needs editing.
3. Connect their help desk (Chatwoot, Intercom, Zendesk or Freshdesk) and their payment provider in its test mode (`03`, `04`). Start with one channel.
4. Load their real help pages into `KNOWLEDGE_DIR` and press **Read the folder now** on the Knowledge page, or paste them there. Import their customers (below). Read the Gaps and the pages that disagree, and have the client fix the pages before anything goes live.
5. Write the refund rule with the client in plain words and enter it on the Policies page. The shipped defaults are a starting point: refunds within 7 days for monthly plans and 14 days for annual plans, one per customer per 12 months, duplicate charges always refunded, every refund approved by a person. Keep what the client agrees to in writing.
6. Agree the first reply targets and whether they count business hours. The defaults are urgent 1 hour, high 4, normal 24 and low 72, counted in business hours (09:00 to 17:00 on weekdays in `OFFICE_TIMEZONE`). A client with round the clock support sets `SLA_CLOCK=round_the_clock`.
7. Agree the tone with the client. The default is plain, friendly and short. Read ten drafts together and note what to change in the pages and in the escalation owners.
8. Name who handles escalations (billing, security and everything else), and what the desk must never answer.
9. Run in draft mode for at least a week. Compare the drafts with what their agents really sent, and add the differences as test cases.
10. Write 60 or more real questions with the right page for each and run `tools/eval_heldout.py`. Show the client the measured result, including the misses.
11. Follow `09-GO-LIVE-CHECKLIST.md`.

## Loading the client's customers

Refund and account answers read each customer's plan and payment facts, so a real install needs them. Make a CSV file (one line per customer) with a header row and import it on the command line with `python -m deskfern.admin import-customers customers.csv` (on Docker: `docker compose exec api python -m deskfern.admin import-customers /tmp/customers.csv`), or as the owner through the API (`POST /api/customers/import`, the file text in a field called `csv`). There is no import button in the app yet. Importing the same file twice updates, it does not duplicate.

| Column | Meaning |
| --- | --- |
| `email` | Required. The address the customer writes from. |
| `name`, `plan`, `interval`, `seats` | Plan name, `monthly`, `annual` or `none`, and the number of seats. A missing plan is `free`. |
| `first_paid`, `last_paid` | Dates like `2026-11-30`. |
| `amount_minor`, `charge_id` | The last charge in the smallest unit (cents) and its id in the payment system. A paying customer needs the charge id so a refund can name it. |
| `refunds_12m` | Refunds already given in the last 12 months. |
| `flag` | Empty, or `duplicate_charge` or `charged_after_cancel` (a billing problem you already know about, which the refund rules always honour). |
| `key` | Optional. Your own id for the customer. |

A row with a problem is skipped and reported by line number; the other rows are saved. The file is limited to 5000 rows, so split a bigger one.

## Handover

- Give the client their own owner and approver sign ins, and a read only sign in (`VIEWER_EMAIL`) for someone who only needs to look, such as a manager. Remove yours.
- Hand over the backup steps in `11` and the name of the person who updates the app (`12`).
- Hand over the secrets inside the client's own hosting account, never by chat or email.
- Hand over the list of pages and who owns each one. An out of date page gives an out of date answer.
- Show them the kill switch (Pause all) and the audit log check.
- Agree who answers when the desk gets something wrong. It will.

## What to put in the client contract

Plain wording, checked by your own lawyer: what is included in setup and in the monthly work; that third party accounts and their costs belong to the client; that the client owns the data, the pages and the accounts; that the client sets the refund rule and the reply targets and approves every refund; that the desk drafts and a person decides, with no promised results; that the client has told their customers (in a privacy notice) that software drafts replies; how long tickets are kept and who can read them; who answers if a reply or a refund goes wrong, and how fast; how either side ends the work and what happens to the data afterwards. This is not legal advice.

## Keep it honest

The demo answer writer is a rule based stand in, not a language model, and its measured scores are in `ANSWER-EVAL.md`. Insights and Health scores are simple and can be wrong. A cited sentence is supported by the page it quotes, which is only as true as the page.

## The daily routine

For whoever works the desk each day:

- **Queue:** work from the top. Red timers are past their reply target.
- **Open a ticket:** read the meter and the sources before the words. If a sentence has no source, do not send it.
- **Send, edit or hand over.** An edit can be saved as a test case with one tick.
- **Approvals:** refunds wait here. You cannot approve your own request.
- **Knowledge:** check the Gaps list weekly. A gap is a question people ask that no page answers well, so write that page.
- **Insights and Health:** read them as hints. The scores are simple and can be wrong.
- **Audit log:** every action with who did it, kept in order. Press Check the chain to see whether history was changed.

The shared daily and weekly checks are in `11-OPERATING-AND-BACKUPS.md`.
