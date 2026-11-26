# Troubleshooting

| You see | Likely cause | Fix |
| --- | --- | --- |
| The web app says the server is not reachable | `API_BASE_URL` wrong or API not running | Check `http://127.0.0.1:8000/health`, then the variable |
| Sign in says wrong email or password | Hash missing or wrong, or demo mode off | Run `tools/hash_password.py`, set `OWNER_PASSWORD_HASH` |
| Sign in says too many tries | Several wrong tries from your address, or someone is failing on purpose at the owner's account and you are signing in from a new address | Wait a minute and try once; from an address that signed in before, the account wide limit does not apply |
| Safari signs you out at once on `http://localhost:3000` | The session cookie is Secure and Safari does not keep it for plain http | Start the web app with `INSECURE_COOKIES=1` on your own computer, never on a server |
| The settings page says sign in limits are shared or every visitor looks like one address | The API does not trust the forwarded visitor address | Set `FORWARDED_ALLOW_IPS` on the API (`06`, `07`, `08`) |
| Every ticket is handed to a person | Empty or tiny knowledge base | Press Read the folder now, or paste pages; check the Knowledge page |
| The API stops at start and names a setting | The example or short `SESSION_SECRET` (with an owner password, or MODE live), an example owner address or company name, no way to send a reply, or no approver login in live mode with real payments | Fix the named setting (`04`); the message says which |
| An email ticket says the sender is not verified | `INBOUND_AUTHSERV_ID` is empty or does not match your mail provider | See `04`, then press Confirm sender on the ticket once you have checked the person |
| A reply cannot be sent, or says it was held | Draft mode or kill switch | Check `MODE` and the Pause all state; send the reply again once both are off, the ticket stayed open |
| A ticket asks "Was this reply delivered?" | The helpdesk or mail server did not confirm in time | Check the helpdesk or the mailbox, then press It was delivered or It was not delivered |
| A refund says approved, not paid yet | It was approved while draft mode or Pause all was on | Turn both off; the worker pays it within a minute, or press Pay approved refunds now on Approvals |
| A ticket named "Too large for the desk to read", "Could not be read by the desk" or "(message from an erased address)" | The desk could not take that message in as a normal ticket (too large with no readable text part, damaged, never stored, or from an address erased on request) | Open the original in the mailbox or helpdesk and answer it there; the note on the ticket says why. To accept an erased address again: `python -m deskfern.admin lift-erasure person@example.com` |
| The Queue shows a Needs attention box | Intake noticed something (a helpdesk answered with something that is not data, a gap in a list, skipped mail) | Read each line; a source's line clears by itself when it works again, the others after 7 days |
| A crawl stays queued | The worker is not running | Start `python -m deskfern.worker` (`05`) |
| A refund stays waiting | Same person asked and tried to approve | Sign in as the approver |
| Chatwoot webhook returns 401 | Wrong secret, or clock more than 5 minutes off | Check the secret and the server clock |
| Crawl skips pages | robots.txt, size or depth limits, or a private address | See the skipped list on the Knowledge page |
| Vercel build cannot find the ui kit | Source outside root not included | See doc 06, step 4 |
| `make check-env` fails | A name missing from your env file | Compare with `.env.example` |
