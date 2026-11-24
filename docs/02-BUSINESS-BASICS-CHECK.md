# Business basics check

Deskfern reads customer messages and drafts replies. Before you point it at a real support inbox, check these. The shared guide for the company, domain, website and mailbox setup is `00-BUSINESS-BASICS.md` in the hub repo. This is general information, not legal advice.

1. **A real support address on a domain you control.** Replies go out under your business name, so the sending address must be yours, with SPF, DKIM and DMARC records set at your DNS host. Deskfern cannot set or check these.
2. **A privacy notice that mentions automated drafting.** Customers should be told that their messages are read by software that drafts replies and that a person approves anything sensitive. What you must say depends on where you operate. Ask someone qualified.
3. **Know what you keep.** Tickets can hold names, order details and sometimes payment hints. Decide how long you keep them and who can read them (see `13-SECURITY-AND-DATA.md`). The demo uses made up customers only.
4. **Write your refund and escalation rules down first.** Deskfern can only ask for a refund, and a second person approves it. The limits in the demo are defaults, not your policy. Replace them with yours in the Policies page before go live.
5. **Help pages that are true today.** Every answer quotes your pages. An out of date page produces an out of date answer. Name one person who owns each page.
6. **A way for a customer to reach a person.** Keep a human path (an address or a form) in every reply. Deskfern hands over angry, legal, safety and money questions on purpose.
7. **Know your rules.** Support records, consumer refunds and automated replies are regulated in some places. Check where you operate before going live.

Run `make check-env` and `python3 tools/doctor.py` for the technical side. They never print a secret.
