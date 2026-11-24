# Contributing

- Run `make test`, `python3 tools/check_docs.py` and `python3 tools/scan_secrets.py` before a pull request.
- Do not edit `packages/` here. It is a shared base copied from the main source.
- Never add a real key, a real customer name or a private link.
- Numbers in docs come from `data/counts.json`. Run `python3 tools/make_counts.py` then `python3 tools/check_docs.py --fix`.
- If you change the answer engine, rerun `tools/eval_heldout.py` and `tools/eval_grounding.py`, and keep the first run files as they are.
