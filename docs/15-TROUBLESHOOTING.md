# Troubleshooting

| You see | Likely cause | Fix |
| --- | --- | --- |
| The web app says the server is not reachable | `API_BASE_URL` wrong or API not running | Check `http://127.0.0.1:8000/health`, then the variable |
| Sign in says wrong email or password | Hash missing or wrong, or demo mode off | Run `tools/hash_password.py`, set `OWNER_PASSWORD_HASH` |
| Every ticket is handed to a person | Empty or tiny knowledge base | Sync a source; check the Knowledge page |
| A reply cannot be sent | Draft mode or kill switch | Check `MODE` and the Pause all state |
| A refund stays waiting | Same person asked and tried to approve | Sign in as the approver |
| Chatwoot webhook returns 401 | Wrong secret, or clock more than 5 minutes off | Check the secret and the server clock |
| Crawl skips pages | robots.txt, size or depth limits, or a private address | See the skipped list on the Knowledge page |
| Vercel build cannot find the ui kit | Source outside root not included | See doc 06, step 4 |
| `make check-env` fails | A name missing from your env file | Compare with `.env.example` |
