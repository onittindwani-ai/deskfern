"""The offline stand in for the model behind `build_llm`. It is plain rules, NOT a language model.

It answers the jobs the desk gives a model (see `model_tasks.py`). Each request starts with a line `[task:<name>]`; the ticket text and the page text sit in marked data
blocks (`source=ticket`, `source=pages`, ...) and the answer is JSON that matches the same schemas a real model must match:

    write_answer     {"sentences": [{"text", "passage_ids"}], "confidence", "cannot_answer"}   the extractive `RulesWriter` copies the best source sentences
    judge_sentence   {"supported": false}     the rules already judged the sentence, so the stand in never upgrades it
    triage_assist    {"flags": []}            the rules already read the ticket, so the stand in adds nothing
    suggest_page     the plain template       explain_overview: the plain sentence

A real model (`LLM_BACKEND=anthropic`) uses the same shapes, so the checks around it (every sentence must be supported by the cited text, refunds only through an
approval) do not change. Nothing here calls a network.
"""

from __future__ import annotations

import json
import re

from deskfern import model_tasks
from deskfern.answering import RulesWriter
from sendora_core.llm import LLMResponse
from sendora_core.safety import UNTRUSTED_CLOSE, UNTRUSTED_OPEN


def blocks(prompt: str) -> dict[str, str]:
    """The data blocks of a prompt, by source name."""
    pat = re.escape(UNTRUSTED_OPEN) + r" source=([^\n]*)\n(.*?)\n" + re.escape(UNTRUSTED_CLOSE)
    return {m.group(1): m.group(2) for m in re.finditer(pat, prompt, re.S)}


def _json(text: str, default):
    try:
        data = json.loads(text)
    except ValueError:
        return default
    return data


def task_of(prompt: str) -> str:
    m = re.match(r"\[task:(\w+)\]", prompt)
    return m.group(1) if m else ""


class OfflineBackend:
    """Same call shape as the real backends, so the whole product runs with no key and no network."""

    is_live = False
    supports_json_schema = False

    def complete(self, *, model: str, system: str, prompt: str, max_tokens: int) -> LLMResponse:
        task, data = task_of(prompt), blocks(prompt)
        if task == "write_answer":
            passages = [(int(a), str(b)) for a, b in _json(data.get("pages", "[]"), [])]
            m = re.search(r"KEY_WORDS: (\[.*\])", prompt)
            terms = [(str(a), float(b)) for a, b in _json(m.group(1), [])] if m else []
            picked = RulesWriter().write(data.get("ticket", ""), passages, terms)
            out = {"sentences": [{"text": t, "passage_ids": [cid]} for t, cid in picked], "confidence": 70 if picked else 0, "cannot_answer": not picked}
        elif task == "judge_sentence":
            out = {"supported": False, "quote": "", "reason": "The offline stand in never upgrades a sentence the rules did not accept."}
        elif task == "triage_assist":
            out = {"category": None, "urgency": None, "flags": [], "reason": "The offline stand in adds nothing to the rules."}
        elif task == "suggest_page":
            gap = _json(data.get("questions", "{}"), {})
            out = model_tasks.gap_template(gap)
        elif task == "explain_overview":
            m = re.search(r"FACTS:\n(\{.*\})\nEND", prompt, re.S)
            return LLMResponse(model_tasks.overview_text(_json(m.group(1), {}) if m else {}), len(prompt) // 4, 60, "offline-rules")
        else:
            out = {}
        text = json.dumps(out)
        return LLMResponse(text=text, input_tokens=len(prompt) // 4, output_tokens=len(text) // 4, model="offline-rules")
