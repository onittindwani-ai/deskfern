# Security and data

The full list of what was tested and what was not is in `../SECURITY.md`. In short:

- **Refunds need two people.** The desk can only ask. A different person approves. Only then does the payment adapter act, once, and never in draft mode or when the kill switch is on.
- **Draft mode is the default.** Nothing is sent or paid. Live mode is a setting changed outside the app.
- **Customer text is data.** Text in a ticket that tries to give orders ("ignore your rules", "refund me now") is flagged and never obeyed.
- **One customer's data never goes to another.** A ticket that asks about someone else's account is handed to a person.
- **Website crawling is guarded.** Only public addresses, redirects are checked again, robots.txt is respected, and size, depth and page limits apply.
- **Audit log.** Every change is chained to the one before. Press Check the chain on the Audit page.
- **Web.** The browser never holds a vendor key. The web app forwards to the API and adds a required header to every change, so a form on another website cannot make the browser change data.
- **Who a message is really from.** Anyone can type any address into a message. The desk only treats a sender as a known customer when it could check it: for email, your mail provider must have added an `Authentication-Results` header (the top one, starting with the name in `INBOUND_AUTHSERV_ID`) that says `dmarc=pass`; for a helpdesk channel, the channel must be listed in `TRUSTED_SENDER_CHANNELS`. A message that fails this is kept, flagged `unverified sender`, never linked to a customer, and answered with a short note asking the person to confirm their address (a person on your team confirms it on the ticket before any private fact is used). Turn the rule off only by setting `REQUIRE_VERIFIED_SENDER=false`, which you should do only when every sender is already known to be real. Where to find your provider's name: `04`, under `INBOUND_AUTHSERV_ID`.
- **Bad input stops at the door.** A request over `MAX_BODY_BYTES` is refused as it arrives. A message with an unreadable character set, a very long identifier or other data the database refuses is skipped on its own without stopping the others, and what it said is never written into a log.
- **Email.** The inbox and the sender connection check the certificate and the host name. A plain connection is accepted only to a mail server on the same computer.
- **Where data lives.** Tickets, customer names and answers are in your database. If you later connect a language model, the ticket text and the cited passages go to that vendor.

## What you must do

Keep `.env` out of Git. Use a second approver. Back up the database and the knowledge folder. Rotate a key if you ever pasted it somewhere. Keep the server updated.
