# Language model path

UNVERIFIED live. Date written: 2026-11-23.

What the code does (`apps/api/src/deskfern/model_tasks.py`): it goes through the base wrapper (`sendora_core.llm`), which sends `POST /v1/messages` to the Anthropic Messages API with the key in `x-api-key`, the `anthropic-version` header, the system prompt, the ticket text and the help page text inside two marked untrusted blocks, and a JSON schema in `output_config.format`. The request and response fields follow the vendor documentation as read for the base on 2026-11-23 (see the hub's FACTS-AND-SOURCES). Models used: `claude-sonnet-5-5` for writing reply sentences and judging a sentence, `claude-haiku-5-5` for the triage second look, the page suggestion and the Insights wording.

Five jobs: write the reply sentences (each with the ids of the pages it relies on and a confidence), judge one sentence the rules could not settle (only to find a quote, which code then checks), a second look at the triage (it can only add caution), a suggested help page for a gap, and plain words for the Insights numbers.

What code does around the model (all tested against a fake server): the groundedness check runs on every sentence a model writes and removes unsupported ones; sentences that give orders, claim an action was done or carry a link that is not on the cited page are dropped first; page text that holds orders is never sent to the model; marker lookalikes cannot close a data block; a model can never refund, approve or send because this module has no access to any of those; a bad answer, a kill switch or a spent budget falls back to the extractive writer.

What was run: the whole path against a fake server on this machine (`mock/anthropic_mock.py`) that checks the request shape, and replay of recorded answers (`data/llm_fixtures/`, made by that fake server, not by a real model). **Never run with a real key.** How a real model writes real replies, its cost per ticket and its error rate are unknown.

Switches: `LLM_BACKEND` (offline, fixture, anthropic), `JUDGE` (rules by default, model lets the model rescue a gray sentence), `TRIAGE_ASSIST` (off by default, model adds the second look). See `04-ENVIRONMENT-VARIABLES.md`.
