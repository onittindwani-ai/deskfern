# Security and data

The full list of what was tested and what was not is in `../SECURITY.md`. In short:

- **Refunds need two people.** The desk can only ask. A different person approves. Only then does the payment adapter act, once, and never in draft mode or when the kill switch is on.
- **Draft mode is the default.** Nothing is sent or paid. Live mode is a setting changed outside the app.
- **Customer text is data.** Text in a ticket that tries to give orders ("ignore your rules", "refund me now") is flagged and never obeyed.
- **One customer's data never goes to another.** A ticket that asks about someone else's account is handed to a person.
- **Website crawling is guarded.** Only public addresses, redirects are checked again, robots.txt is respected, and size, depth and page limits apply.
- **Audit log.** Every change is chained to the one before. Press Check the chain on the Audit page.
- **Web.** The browser never holds a vendor key. The web app forwards to the API and adds a required header to every change, so a form on another website cannot make the browser change data.
- **Where data lives.** Tickets, customer names and answers are in your database. If you later connect a language model, the ticket text and the cited passages go to that vendor.

## What you must do

Keep `.env` out of Git. Use a second approver. Back up the database and the knowledge folder. Rotate a key if you ever pasted it somewhere. Keep the server updated.
