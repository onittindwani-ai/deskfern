# Answer evaluation, honestly

## Method

Before any answer code existed, 63 questions with the correct source page were written and committed (`data/eval/heldout_questions.json`), and 79 sentences were labelled supported or not (`groundedness_labels.json`). The first run of each was saved untouched, then the engine was tuned, then run again. After tuning, the set is no longer held out, because the same author looked at the failures.

## Held out questions (63)

| Run | Correct | Escalation precision | Escalation recall | Out of scope refused | Citations checked | Invalid |
| --- | --- | --- | --- | --- | --- | --- |
| First, untouched | 13 | 0.243 | 0.75 | 6 of 6 | 4 | 0 |
| After tuning (not held out) | 16 | 0.216 | 0.667 | 6 of 6 | 20 | 0 |

First run by kind: single page questions 0 of 26, two page questions 0 of 14, unanswerable 7 of 10, out of scope 6 of 6, someone else's account 0 of 4, conflicting pages 0 of 3.

What it means: the offline stand in matches words, so a question phrased differently from the page often gets handed to a person. That is safe but not useful. The desk almost never made things up (no invalid citations), and it refused out of scope questions. A real model or real embeddings are needed for good coverage and were not measured.

## Groundedness checker (79 labelled sentences)

| Run | Precision | Recall |
| --- | --- | --- |
| First | 0.893 | 0.625 |
| After tuning (not independent) | 1.0 | 0.825 |

Precision is how often a sentence it accepted was really supported. Recall is how many supported sentences it accepted.

## Seed check (developer score, not independent)

83 of 121 seeded tickets handled as planned. Escalation precision about 0.63 and recall about 0.88. This was tuned on the seed.

## Independent sets

Two larger sets written by separate AI test authors who never saw the code, with the first run kept untouched, are reported in `INDEPENDENT-EVAL.md`.

## Run it on your own pages

`python3 tools/eval_heldout.py` with your own questions file, and `python3 tools/eval_grounding.py` with your own labels. Write the questions first.
