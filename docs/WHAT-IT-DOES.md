# What each part does

## Knowledge
You add sources: a folder of Markdown, text or PDF files, a Google Drive folder, Notion pages, or a website (crawled politely, public pages only). Each page is cut into passages. Every passage keeps its page title, its section and a one line description of where it sits, so a passage about "30 days" still says which policy it belongs to. Search combines word matching and a simple word similarity score.

## Tickets in
A ticket arrives from Chatwoot, Intercom, Zendesk, Freshdesk or an email inbox. Offline, the demo loads a seeded set. Each ticket is cleaned, checked for duplicates and for text that tries to give the desk orders (such as "ignore your rules"), which is treated as a customer's words and never as an instruction.

## Triage
A rule set decides: answer, ask a person, refuse (not a support question), or escalate (legal, safety, security or outage words, a refund, an account that belongs to someone else; an angry customer gets a high urgency mark). Each decision lists the reasons.

## Answers with proof
The desk writes a reply from the passages it found. A checker then scores every sentence against the cited passages: the words, the numbers (in the right order), who or what each number belongs to, negations such as "not" and "never", and the subject. A sentence the passages do not support is removed or the whole ticket is handed to a person. The score is shown in the meter, never hidden.

## Refunds
The refund rules are policy text you can edit and test in the **Policies** simulator. The desk can only ask for a refund. A person approves it (a different person from the one who asked). Only then does the payment adapter act, once, and it is blocked in draft mode and by the kill switch.

## Learning from agents
When an agent edits a draft, Deskfern stores the question, the edit and the sources as a test case. The **Test cases** page runs them again after any change.

## Gaps, health and insights
Questions the desk could not answer well are grouped into gaps (what page is missing). Customer health and a churn warning are simple scores from recent tickets and are labelled as guesses.
