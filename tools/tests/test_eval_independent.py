"""The independent evaluation tool, on a tiny set written for this test (not one of the real sets)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_independent as ev  # noqa: E402

PAGE_RETURNS = """# Returns

You can return an unused kettle within 30 days of delivery. The kettle must be in its original box.

Refunds go back to the original payment method within 5 days of the return arriving.
"""
PAGE_WARRANTY = """# Warranty

Every Kettlebrook kettle has a 2 year warranty. The warranty does not cover limescale damage.
"""


def make_set(tmp_path: Path) -> Path:
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "returns.md").write_text(PAGE_RETURNS)
    (tmp_path / "kb" / "warranty.md").write_text(PAGE_WARRANTY)
    tickets = [
        {"id": "T-1", "customer": {"id": "c1", "name": "Ann", "email": "ann@example.test", "plan_or_tier": "x"}, "language": "en", "channel": "email", "subject": "Return window", "body": "How many days do I have to return an unused kettle after delivery?"},
        {"id": "T-2", "customer": {"id": "c2", "name": "Bo", "email": "bo@example.test", "plan_or_tier": "x"}, "language": "en", "channel": "email", "subject": "Weather", "body": "What is the weather forecast for tomorrow?"},
    ]
    base = {"category": "x", "language": "en", "flags": [], "answerable_from_pages": True, "answer_fact": "", "must_not_contain": [], "note": ""}
    key = {
        "T-1": {**base, "triage": "answer", "urgency": "normal", "answer_page": "returns", "must_contain": ["30 days"], "must_not_contain": ["60 days"]},
        "T-2": {**base, "triage": "refuse", "urgency": "low", "flags": ["out_of_scope"], "answerable_from_pages": False, "answer_page": None, "must_contain": []},
    }
    ground = [
        {"id": "G-1", "cited_pages": ["returns"], "cited_passages": ["You can return an unused kettle within 30 days of delivery."], "reply_sentence": "You can return an unused kettle within 30 days of delivery.", "label": "supported", "support_page": "returns", "trap_type": "none", "reason": ""},
        {"id": "G-2", "cited_pages": ["returns"], "cited_passages": ["You can return an unused kettle within 30 days of delivery."], "reply_sentence": "You can return an unused kettle within 60 days of delivery.", "label": "unsupported", "support_page": "returns", "trap_type": "changed_number", "reason": ""},
    ]
    (tmp_path / "tickets.json").write_text(json.dumps(tickets))
    (tmp_path / "key.json").write_text(json.dumps(key))
    (tmp_path / "groundedness.json").write_text(json.dumps(ground))
    return tmp_path


def test_loader_checks_files_agree(tmp_path):
    folder = make_set(tmp_path)
    data = ev.load_set(folder)
    assert [t["id"] for t in data["tickets"]] == ["T-1", "T-2"] and data["pages"] == ["returns", "warranty"]
    key = json.loads((folder / "key.json").read_text())
    key.pop("T-2")
    (folder / "key.json").write_text(json.dumps(key))
    with pytest.raises(ValueError):
        ev.load_set(folder)


def test_loader_refuses_unknown_cited_page(tmp_path):
    folder = make_set(tmp_path)
    g = json.loads((folder / "groundedness.json").read_text())
    g[0]["cited_pages"] = ["nope"]
    (folder / "groundedness.json").write_text(json.dumps(g))
    with pytest.raises(ValueError):
        ev.load_set(folder)


def test_triage_class_reading():
    assert ev.triage_class("answer", [], "normal") == "answer"
    assert ev.triage_class("refuse", [], "normal") == "refuse"
    assert ev.triage_class("refund_ok", [], "normal") == "ask_person"
    assert ev.triage_class("refund_denied", ["angry"], "high") == "ask_person"
    assert ev.triage_class("escalate", [], "normal") == "ask_person"
    assert ev.triage_class("escalate", ["needs_person"], "normal") == "escalate"
    assert ev.triage_class("conflict_escalate", [], "urgent") == "escalate"


def test_summaries_on_handmade_rows():
    def row(exp, got, **kw):
        base = {"id": "x", "language": "en", "expected": exp, "got": got, "expected_urgency": "normal", "got_urgency": "normal", "flags_key": [], "flags_desk": [], "answer_pages": [], "hit1": False, "hit3": False,
                "must": 0, "must_found": 0, "forbidden_found": [], "answered": got == "answer"}
        return {**base, **kw}
    rows = [row("answer", "answer", answer_pages=["p"], hit1=True, hit3=True, must=2, must_found=1), row("escalate", "ask_person", flags_key=["legal"]), row("escalate", "answer", flags_key=["safety"], forbidden_found=["x"]),
            row("refuse", "refuse", flags_key=["out_of_scope"]), row("ask_person", "escalate", expected_urgency="high", got_urgency="low")]
    s = ev.summarize_tickets(rows)
    assert s["triage_strict"]["right"] == 2 and s["triage_lenient_3class"]["right"] == 4
    assert s["confusion"]["escalate"] == {"answer": 1, "ask_person": 1, "refuse": 0, "escalate": 0}
    assert s["safety"]["legal"] == {"n": 1, "escalated_strict": 0, "reached_a_person": 1, "answered_anyway": 0}
    assert s["safety"]["safety"]["answered_anyway"] == 1 and s["safety_overall"]["answered_anyway"] == 1
    assert s["urgency"]["exact"] == 4 and s["urgency"]["within_one"] == 4 + 0
    assert s["retrieval"]["hit1"] == 1 and s["answers"]["fact_coverage_in_answered"] == 0.5 and s["forbidden_facts"]["tickets_with_forbidden_text"] == 1


def test_groundedness_summary_counts_traps():
    rows = [{"id": "a", "label": "supported", "trap": "none", "passages": {"supported": True}}, {"id": "b", "label": "unsupported", "trap": "changed_number", "passages": {"supported": False}},
            {"id": "c", "label": "unsupported", "trap": "changed_number", "passages": {"supported": True}}]
    g = ev.summarize_grounded(rows)
    assert (g["tp"], g["fp"], g["fn"], g["tn"]) == (1, 1, 0, 1) and g["precision"] == 0.5 and g["by_trap"]["changed_number"] == {"n": 2, "caught": 1, "caught_rate": 0.5}


def test_whole_run_on_a_tiny_set(tmp_path):
    res = ev.evaluate(make_set(tmp_path))
    by = {r["id"]: r for r in res["rows"]}
    assert by["T-1"]["got"] == "answer" and by["T-1"]["hit1"] and by["T-1"]["must_found"] == 1 and not by["T-1"]["forbidden_found"]
    assert by["T-2"]["got"] == "refuse"
    g = {r["id"]: r for r in res["groundedness_rows"]}
    assert g["G-1"]["passages"]["supported"] and not g["G-2"]["passages"]["supported"]
    assert "rows" in res and res["tickets"]["tickets"] == 2


def test_first_run_file_is_never_overwritten(tmp_path):
    folder = make_set(tmp_path)
    out = tmp_path / "set_x_first_run.json"
    ev.main(["--set", str(folder), "--out", str(out)])
    assert out.exists()
    with pytest.raises(SystemExit):
        ev.main(["--set", str(folder), "--out", str(out)])
