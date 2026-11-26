# Independent evaluation: two test sets written by separate AI test authors who never saw the code

This page reports how the desk did on two sets of tickets and help pages that were written by separate AI test authors who read only the product description. They never saw the code, the seed data or the earlier tests. Everything in the sets is sample data. The files are in `data/eval/independent/` and the tool is `tools/eval_independent.py`.

The desk's writer, triage and checker in this repo are rules, not a language model. These numbers say how good the rules are. They say nothing about a live model (see `14-LIMITS-AND-HONEST-NOTES.md`).

## Plain reading

1. On set A, which nobody had tuned for, the desk read the ticket correctly (answer, ask a person, refuse or escalate) for <!--n:independent.a_first.strict_right-->46<!--/n--> of <!--n:independent.a_first.tickets-->78<!--/n--> tickets on the first run. On set B, run once after the general fixes made for A, it was <!--n:independent.b_first.strict_right-->49<!--/n--> of <!--n:independent.b_first.tickets-->88<!--/n-->. That is about 6 in 10. It is far better than the 13 of 63 the old self written questions gave, but those two numbers are not the same test.
2. The weakest class is escalation. On the first run of set A the desk escalated 1 of 12 tickets that the authors marked as needing a hand over (anger, law, safety, outage, security). The triage had no words for safety, security or outage at all.
3. The groundedness check is the strong part for catching mistakes (it rejected about 86 percent of the wrong sentences on both sets at first) and the weak part for letting right sentences through (recall <!--n:independent.b_first.ground_recall-->0.549<!--/n--> on set B).
4. The desk still answers some tickets that need a person: on set B it answered 10 of 13 tickets where no page holds the answer, because a related page was close enough. This is the most important thing to watch with real tickets.
5. After the first runs the safety misses of both sets were read and fixed with general rules (details below). Because of that, set A and set B are no longer held out. The first run numbers are the honest ones.

## How it was run

- Each set has help pages (`kb/*.md`), tickets, a key and sentences to judge. The pages are loaded into a fresh database through the desk's own folder ingestion. Each ticket goes through `triage.triage` and `answering.draft_for`. Nothing is sent.
- The reading of the desk's outcomes as the authors' four words is fixed in the tool before any run. `answer` is answer, `refuse` is refuse, a refund outcome is `ask_person`, and an escalation counts as `escalate` when the desk itself marked the ticket as angry, needing a person or urgent, and as `ask_person` otherwise. A second, lenient reading merges `ask_person` and `escalate`.
- The sets carry no payment data, so refund tickets are scored only on the decision.
- The groundedness check judges each sentence against only the passages cited in the set (the primary number). A second reading uses every chunk of the cited pages.
- Order of work: the tool and its tests were written and committed, set A was copied in, set A was run once and the raw result saved and committed, then the offline stand in was improved from general language knowledge and the desk's own seed pages. Set B was run once after that, saved and committed. Then set A was run again.
- What was looked at, honestly: before any run only the authors' README. After the first runs, counts only (which kinds of tickets failed and why the desk handed them over). Then the safety misses of both sets (tickets about law, safety, security, outage, another customer's data and instructions aimed at the system) were read in full and general word lists and patterns were added. Every other failing ticket was not read.

## Headline table

| | Old self written | Set A first run | Set A after changes (not held out) | Set B first run | Set B after changes (not held out) |
| --- | --- | --- | --- | --- | --- |
| Tickets | 63 questions | 78 | 78 | 88 | 88 |
| Triage right (4 words) | 13 of 63 (answers) | <!--n:independent.a_first.strict_right-->46<!--/n--> | <!--n:independent.a_final.strict_right-->55<!--/n--> | <!--n:independent.b_first.strict_right-->49<!--/n--> | <!--n:independent.b_final.strict_right-->57<!--/n--> |
| Triage right (3 words, lenient) | | 53 | 60 | 53 | 60 |
| Urgency exactly right | | 36 | 37 | 30 | 31 |
| Urgency within one step | | 73 | 75 | 84 | 84 |
| Right page first (of tickets with a page) | | 48 of 65 | 47 of 65 | 52 of 82 | 53 of 82 |
| Right page in the first 3 | | 56 of 65 | 57 of 65 | 67 of 82 | 67 of 82 |
| Answered, of those that should be | | 30 of 44 | 29 of 44 | 41 of 54 | 40 of 54 |
| Answered with every required fact | | 14 of 44 | 16 of 44 | 28 of 54 | 27 of 54 |
| Replies holding a forbidden fact | | 2 of 78 | 2 of 78 | 6 of 88 | 5 of 88 |
| Groundedness precision | 0.893 | <!--n:independent.a_first.ground_precision-->0.737<!--/n--> | 0.8 | <!--n:independent.b_first.ground_precision-->0.757<!--/n--> | 0.757 |
| Groundedness recall | 0.625 | <!--n:independent.a_first.ground_recall-->0.683<!--/n--> | 0.683 | <!--n:independent.b_first.ground_recall-->0.549<!--/n--> | 0.549 |

The old self written numbers (first run, before any tuning) were 13 correct answers out of 63 held out questions and groundedness precision 0.893 and recall 0.625 on 79 sentences labelled by the same author. Those were written by the same author who wrote the rules, so they were easier in some ways and harder in others. A set A rerun after the first batch of changes (before the safety read) is saved too (`set_a_after_improvements.json`: 46 of 78).

## Triage safety numbers (the ones that matter most)

The question is: did a ticket that needs a person reach one, and was it never answered by itself?

| Kind of ticket (by the authors' flag) | Set A first run | Set A after changes | Set B first run | Set B after changes |
| --- | --- | --- | --- | --- |
| Legal (3 in A, 5 in B) reached a person | 3 of 3 | 3 of 3 | 5 of 5 | 5 of 5 |
| Legal marked as escalate | 1 of 3 | 3 of 3 | 3 of 5 | 4 of 5 |
| Safety (2 in A, 5 in B) reached a person | 1 of 2 | 2 of 2 | 2 of 5 | 4 of 5 |
| Safety answered by itself | 1 | 0 | 2 (and 1 more went to a refusal) | 0 |
| Security (2 in A, 1 in B) reached a person | 1 of 2 | 2 of 2 | 1 of 1 | 1 of 1 |
| Outage (2 in A, 1 in B) reached a person | 1 of 2 | 2 of 2 | 1 of 1 | 1 of 1 |
| Angry (3 in A, 4 in B) reached a person | 1 of 3 | 2 of 3 | 3 of 4 | 3 of 4 |
| Another customer's data (5 and 5) never answered | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 |
| Another customer's data, flagged by name | 1 of 5 | 4 of 5 | 1 of 5 | 4 of 5 |
| Instruction aimed at the system (4 and 4) flagged | 4 of 4 | 4 of 4 | 2 of 4 | 4 of 4 |
| Bare instruction with no real question, refused | 1 of 3 | 3 of 3 | 0 of 2 | 1 of 2 |
| Out of scope (2 and 2) refused | 1 of 2 | 2 of 2 | 1 of 2 | 2 of 2 |
| All safety class tickets answered by themselves | 3 of 16 | 1 of 16 | 2 of 18 | 0 of 18 |

Notes on the table:

- "Reached a person" means the desk escalated or marked the ticket for a person. Only a person can approve a refund in any case, and nothing is sent in draft mode.
- The "after changes" columns are not held out: the misses of this kind were read and fixed.
- One angry ticket in each set still does not reach a person: the desk drafts an answer for an angry customer with a high urgency flag and does not hand the ticket over. The start guide used to say it hands over; the guide was corrected to say what the code does. This is a product choice the implementer can change.
- Another customer's data and instruction tickets: no reply held a forbidden string on set A. On set B one instruction ticket produced a reply with a forbidden string on the first run.

## Where it fails (first runs)

Confusion on set A, first run (rows are the authors' word, columns the desk's):

| | answer | ask_person | refuse | escalate |
| --- | --- | --- | --- | --- |
| answer (44) | 30 | 11 | 3 | 0 |
| ask_person (13) | 1 | 10 | 1 | 1 |
| refuse (9) | 0 | 4 | 5 | 0 |
| escalate (12) | 3 | 6 | 2 | 1 |

Confusion on set B, first run:

| | answer | ask_person | refuse | escalate |
| --- | --- | --- | --- | --- |
| answer (54) | 41 | 9 | 4 | 0 |
| ask_person (13) | 10 | 1 | 2 | 0 |
| refuse (8) | 1 | 4 | 2 | 1 |
| escalate (13) | 2 | 4 | 2 | 5 |

Groundedness by kind of mistake, first runs (wrong sentences caught, of the wrong ones in that kind; the after columns are the numbers after the changes):

| Kind of mistake | Set A first | Set A after | Set B first | Set B after |
| --- | --- | --- | --- | --- |
| Changed number | 9 of 9 | 9 of 9 | 10 of 11 | 10 of 11 |
| Wrong unit | 2 of 6 | 5 of 6 | 8 of 8 | 8 of 8 |
| Negation flipped | 18 of 19 | 18 of 19 | 16 of 18 | 16 of 18 |
| Overclaim | 8 of 9 | 8 of 9 | 7 of 7 | 7 of 7 |
| Wrong subject | 9 of 10 | 9 of 10 | 6 of 10 | 6 of 10 |
| Stale information | 5 of 6 | 5 of 6 | 1 of 3 | 1 of 3 |
| Wrong plan | 6 of 7 | 6 of 7 | none | none |
| Detail not in the pages | 3 of 3 | 3 of 3 | 3 of 3 | 3 of 3 |
| True but not in the pages | 3 of 3 | 3 of 3 | 1 of 1 | 1 of 1 |
| Partial negation | 0 of 1 | 0 of 1 | none | none |

Set B was run after the unit fix, so its first run already had it.

## What was changed between the runs (general language knowledge only)

- Number words read as digits (two weeks is 2 weeks), "one" before a time unit, and a numbers with units check: every number with a time, size, percent or money unit and every "per minute" must carry the same unit in the source. Currencies are kept apart.
- Strong words (all, always, unlimited, guaranteed, instantly, forever, must) must be in the source before a sentence may use them.
- Irregular forms share a stem (paid and pay, bought and buy, people and person). Words that differ only by ending (deliver and delivery, ship and shipment) count as the same word when scoring how well a page covers a question. More everyday synonyms (reimburse, fee, terminate, ship, warranty and others). More filler words are ignored.
- The question of whether a ticket is about the product is now answered with words from the page titles and names the help pages themselves use, not only a fixed list written for the demo company. Refusal text uses the company name from the settings, not a fixed name.
- Pages whose name or title says archived, old, legacy, superseded or deprecated lose against a current page, and any page named changelog settles a conflict.
- Triage: words for law, safety, security incidents and outages that hand the ticket to a person, calmer wording lowers urgency, an incident raises it, an instruction with no question left is refused, more patterns for another customer's data and for instructions aimed at the system, more out of scope topics.

## Weaknesses

- It answers tickets that need a person when a nearby page exists (10 of 13 on set B). The check that every sentence is supported by the page cannot tell that the page answers a different question.
- Non English tickets are all handed to a person with a short note. That is safe and it is why answered counts are lower than they could be.
- Urgency is guessed from a few words. Exact agreement is about 4 in 10 on both sets, and within one step it is above 9 in 10.
- Groundedness recall is low: correct paraphrases of a page are often rejected (set B recall <!--n:independent.b_first.ground_recall-->0.549<!--/n-->). The desk then hands the ticket over, which is safe and slow.
- The test authors' labels came from one model each and were not reviewed by a person. Some labels are borderline (the authors say so). Angry and refund tickets are read differently by the product and by the key.
- No live model was run. A model can write better replies, and the same groundedness check runs on its reply (see `14-LIMITS-AND-HONEST-NOTES.md`).

## Run it on your own pages

Write your own set in the same shape (`kb/`, `tickets.json`, `key.json`, `groundedness.json`; the schema is in `data/eval/independent/README-heldout-authors.md`), then run `python3 tools/eval_independent.py --set path/to/your/set --out your_first_run.json`. Save the first run before you change anything and report that number.
