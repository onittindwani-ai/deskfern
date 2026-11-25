import json
import subprocess

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


def test_first_run_files_exist_and_say_what_they_are():
    first = json.loads((ROOT / "data/eval/heldout_first_run.json").read_text())
    assert first["questions"] >= 60 and "rows" in first
    g = json.loads((ROOT / "data/eval/groundedness_first_run.json").read_text())
    assert g["n"] >= 60 and "precision" in g


def test_heldout_questions_were_written_before_the_answer_code():
    log = subprocess.run(["git", "log", "--format=%H %s", "--reverse"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    subjects = [line.split(" ", 1)[1] for line in log if " " in line]
    if not any("held out question set" in s for s in subjects):
        pytest.skip("This check reads the original development history. A release clone carries one commit, so there is nothing to compare.")
    held = next(i for i, s in enumerate(subjects) if "held out question set" in s)
    engine = next(i for i, s in enumerate(subjects) if "cited answers" in s)
    assert held < engine


def test_tool_scores_are_valid_and_the_doc_is_current():
    r = subprocess.run([PY, str(ROOT / "tools/score_tools.py"), "--check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_seed_counts_match_the_files():
    c = json.loads((ROOT / "data/seed_counts.json").read_text())
    assert c["tickets"] == len(json.loads((ROOT / "data/seed.json").read_text())["tickets"]) == len(json.loads((ROOT / "data/answer_key.json").read_text()))
    assert c["documents"] == len(list((ROOT / "data/kb").glob("*")))
