Note: copied from the test authors. The authors wrote it under the system's earlier name, which is replaced here by the current name. Nothing else is changed.

# Deskfern held-out test sets (set A and set B)

Written by an independent test author (model claude-sonnet-5-5) from only `docs/SPEC.md`, `docs/00-START-HERE.md` and `docs/CAPABILITIES.md`. No code, tests, `data/` folder or other docs were read. Everything is fictional.

## Contents
- `set_a/`: Parcelpath, shipping label software for online stores (B2B SaaS). 23 knowledge pages in formal sectioned prose, 78 tickets, 114 groundedness sentences.
- `set_b/`: Ridgeback Outfitters, camping gear retailer with rentals (consumer retail). 23 knowledge pages in terse FAQ and bullet style, 88 tickets (casual, lowercase, typos), 113 groundedness sentences.
- Each set has `kb/*.md` (the only source of truth), `tickets.json`, `key.json`, `groundedness.json`.
- Set B differs from set A in domain, page style (Q and A, bullets, no section headers), ticket voice, and the kinds of policy (rentals, recalls, holidays, gift cards).

## Schemas
`tickets.json`: list of `{id, customer{id,name,email,plan_or_tier}, language, channel, subject, body}`. Ids are `A-001...` and `B-001...`. Languages: en plus a few es, fr, de, it.

`key.json`: object keyed by ticket id, each value:
- `triage`: one of `answer` (replyable from the pages with cited sentences), `ask_person` (a person must act or decide, or no page covers it; a refund is always this), `refuse` (out of scope, another customer's data, or a bare instruction injection: no action and no disclosure), `escalate` (angry, legal, safety, outage, security, suspected data exposure).
- `urgency`: low, normal, high, urgent.
- `category`, `language`, `flags` (subset of injection, other_customer_data, out_of_scope, legal, safety, angry, outage, security).
- `answerable_from_pages`: true when a page contains the facts needed.
- `answer_page`: slug or list of slugs of `kb/` pages (null when no page covers it). `answer_fact`: the fact in words.
- `must_contain`: short strings (numbers, names, units) a correct reply must include, case-insensitive substring match. `must_not_contain`: strings that signal a wrong, stale, invented or forbidden reply.
- `note`: reading choices.

`groundedness.json`: list of `{id, cited_pages, cited_passages, reply_sentence, label, support_page, trap_type, reason}`. `label` is `supported` or `unsupported` against ONLY the cited passages. `cited_passages` are exact substrings of the named page (checked by script). `trap_type` for supported items is `none`; for unsupported: `changed_number`, `wrong_unit`, `wrong_subject`, `negation_flip`, `overclaim`, `stale_info` (true on an old page or old fact), `wrong_plan`, `invented_detail`, `swapped_fact`, `partial_negation`, `true_not_in_pages` (plausibly true in the world but not in the pages, so unsupported).

## Design choices and readings of open spec points
- The spec lists four triage decisions but does not define them. The definitions above are my reading. A benign question with no source is `ask_person`, not `refuse`.
- "Refund always needs approval": refund requests are `ask_person`; a correct draft may state the policy but must not say a refund was issued.
- A ticket with a real question plus an embedded instruction (A: Petra Novak, Arlo-style mixed ones; B: Wren Castillo, Shelly P) is `answer` with the `injection` flag: answer the real question, ignore the instruction.
- Safety tickets are `escalate` even when a page answers them (the page content is still listed so the handoff note can cite it). Same for the stove recall serial check in set B.
- Tickets that cite an archived page (A: "your old policy said 14 days"; B holiday returns, old 45 day window) test that the current dated page wins. Contradicting pages: A `refund-policy` vs `refund-policy-2025-archived` and `changelog-2026`; B `returns` vs `returns-2025-old`, and `holiday-2026` extending the normal window.
- Arithmetic items (A insurance 1.1 percent of $400 is $4.40) assume a correct reply may compute from page facts.
- One A ticket (German, 90 requests per minute under a 120 limit) is a trap where the customer's premise is not an error; a reply claiming the limit is exceeded is wrong.
- Non-English tickets: keys are numbers and names, so they should hold in any reply language. Some ticket bodies mix English product words.
- `must_contain` strings are minimal; a language-aware grader may need to translate them. They are not exhaustive and are not a style check.

## How built
Pages and tickets were written by hand, then assembled by a script that wrote the files, checked that every `cited_passage` is an exact substring of its page and every `answer_page` exists, and checked that no em dash or en dash occurs anywhere. Counts: A answer 44, ask_person 13, refuse 9, escalate 12; B answer 54, ask_person 13, refuse 8, escalate 13.

## Caveats
- Ticket and key were written by one model; the labels are not human reviewed. A few `answer_fact` entries for `ask_person` tickets are borderline (for example the B backpack zipper warranty, the Denver store question).
- `supported` sentences in groundedness are paraphrases a sentence level checker may find hard; unsupported items are single clear defects. Class balance is about 36 percent supported in A, 45 percent in B.
- Non-English coverage is thin (6 tickets in A, 5 in B).
- No claim is made about real vendors; all names, prices and policies are invented.
