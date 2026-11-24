"""Triage by plain rules: category, urgency, sentiment, language and safety flags. This is an offline stand in, NOT a language model.

Everything in a ticket is data. The injection scan only marks text and removes it from the question that is searched; it never obeys it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from deskfern.textproc import words

INJECTION = [r"ignore (all |your |the |any )?(previous |prior |above )?(rules|instructions|policy|policies)", r"forget (the |your |all )?(policy|rules|instructions)", r"\bsystem\s*:", r"\bassistant\s*:", r"you are (now )?in (admin|developer|god) mode",
             r"you are now allowed", r"(print|reveal|show|repeat) (me )?(your )?(hidden |secret |system )?(instructions|prompt)", r"<!--.*?-->", r"approve (the |a |any )?refunds? without", r"mark this ticket (as )?resolved",
             r"admin mode", r"override (the )?(policy|rules)", r"(list|give me|send me) every customer",
             r"\bdisregard\b", r"\bnew instructions?\b", r"\bact as (the |an? )?(admin|manager|system|unrestricted)", r"\bpretend (that )?(you|to be)\b", r"(reveal|print|output|show).{0,30}(system prompt|your (rules|configuration|instructions))",
             r"\bbypass (the )?(policy|rules|approval|check|review)", r"\[(system|admin|assistant)\]", r"-{3,}\s*(system|instructions?)", r"(refund|pay|credit|send).{0,40}without (checking|approval|review|asking|a person)", r"\bjailbreak\b", r"do anything now", r"\bnote to (the )?(ai|assistants?|bots?|models?|llms?|agents?)\b", r"\bwhen (a |any )?(customer|user)s? (asks?|writes?|mentions?).{0,80}\b(say|tell|reply|respond|answer|send)\b", r"\b(reply|answer|respond|say|output) only (with|in)\b", r"\bonly (reply|answer|respond) with\b", r"\b(ai|assistant|bot|chatbot)\b[,:]? (please )?(print|reveal|give|apply|ignore|send|generate|issue|grant|approve)\b",
             r"\b(vergiss|ignoriere|ignora|ignorez|oublie|olvida)\b.{0,40}(anweisung|regeln|instruc|r[eè]gles|instructions)"]
PRIVACY = [r"(owner|ceo|assistant|partner|colleague|boss|employee|ex[- ]\w+( \w+)?) (of|at|from|for)\b.{0,60}\b(workspace|account|company|labs|legal|logistics|events)\b", r"\b(her|his|their|colleague'?s|owner'?s) (invoices?|receipts?|billing|card|password|email)",
           r"\b(list|tell me) (the )?(members|users|emails?) (of|for)\b", r"on file for \S+@", r"\bex[- ](employee|colleague|business partner)\b", r"(card details|last four|billing history|password)\b.{0,40}\b(owner|account of|workspace)\b",
           r"what email does the .{0,40} owner", r"(owner|member)s? of the .{0,30} workspace",
           r"\b(paste|send|give|tell|share) (me )?(his|her|their|my colleague'?s|the (account holder'?s|owner'?s))\b.{0,15}\b(api key|key|password|token|login|credentials|address|phone|email)\b", r"\b(list|names|emails|addresses|phone numbers) of (all |every |everyone |your |the )*(top )?\d* ?(customers?|merchants?|clients?|users?|buyers?|everyone|who)\b",
           r"\b(address|phone( number)?|email|name)s? (and|or) (phone( number)?|email|address|name)s? (for|of) (tracking|order|parcel|package|shipment)", r"\b(what|which) (card|address|email|phone)\b.{0,12}\b(did|does) (he|she|they|someone)\b",
           r"\b(ex|neighbou?r|friend|colleague|brother|sister|boss|partner|husband|wife|someone|a customer|another customer)\b[^.?!]{0,40}\b(ordered|bought|purchased|placed|signed up|registered|booked)\b[^.?!]{0,80}\b(address|card|phone|email|details|number)\b",
           r"\b(someone else'?s|another (customer|user|person|member|buyer|shopper)'?s|other (customers|users|buyers|shoppers)'?|a (customer|buyer|shopper)'?s|my (friend|neighbou?r|ex|brother|sister|mother|father|wife|husband|boss|client)'?s)\b[^.?!]{0,40}\b(order|orders|address|phone|email|number|details|account|invoice|history|password|card|tracking|purchase|data|record|info|information)\b"]
REFUND_REQUEST = re.compile(r"(please refund|refund (me|my|the|it|that|this)|want .{0,30}refund|money back|get (a |my )?refund|need .{0,20}refund|refund pls|all of it back|needs refunding|reimburse|charged (me )?twice|charged again|two charges|double charge|extra charge)", re.I)
POLICY_QUESTION = re.compile(r"(refund (window|policy|rules?|period)|how many days|what is the refund|is that right|page says|faq says|your policy)", re.I)
HYPOTHETICAL = re.compile(r"\b(if|when|should) i (cancel|downgrade|switch|leave|stop|quit|pause|close)\b", re.I)  # "If I cancel today, do I get money back?" asks what would happen, it does not ask for a refund
OUT_OF_SCOPE = [r"\b(weather|forecast)\b", r"\bessay\b", r"\brecipe\b", r"\b(stock|stocks|shares|crypto)\b.{0,30}\b(buy|sell|invest)|\bwhich stocks?\b", r"\b(medicine|medical|diagnos\w*|headache|rash|symptom)\b", r"\bjoke\b", r"\bcover letter\b", r"\btranslate\b",
                r"\blegal advice\b", r"\b(world cup|election|president)\b", r"\bname for my (cat|dog|baby)\b", r"\b(lasagna|dinner|cooking)\b", r"\b(my|their) (notely|evernote|onenote|notion) account\b",
                r"without them knowing|without (his|her|their) (knowledge|permission)", r"\bhomework\b", r"\b(pizza|restaurants?|hotels?|flights?|taxes|tax return|mortgage|visa application)\b",
                r"\b(poem|song lyrics|horoscope|trivia|capital of|sports? scores?|movie recommendations?|bitcoin|lottery|dating advice)\b", r"\bwho (won|is the (president|ceo of (google|apple|microsoft)))\b", r"\bwrite (me )?(a |an )?(story|poem|essay|script|python|program|code)\b"]
LEGAL = [r"\b(lawyer|attorney|solicitor|sue|suing|lawsuit|legal action|legal team|court|small claims|regulator|ombudsman|data protection (authority|officer)|subpoena|cease and desist|trading standards|consumer protection)\b", r"report (you|this) to (the |my )?(authorities|regulator|police|bank)", r"\bgdpr\b|\bccpa\b|data subject (access )?request|subject access request|right to (erasure|be forgotten|access)|article 15\b"]
SAFETY = [r"\b(fire|flames?|smoke|smoking|burn(s|ed|t|ing)?|burnt|scald\w*|overheat\w*|explod\w*|sparks?|sparking|electrocut\w*|electric shock|injur\w+|bleeding|hospital|poison\w*|choking|carbon monoxide|gas leak|recall(ed)?|unsafe|dangerous|hazards?|melted|caught fire|allergic reaction|swallowed|swollen|swelling|bulging|hissing)\b", r"\bgas\b.{0,25}\b(smell|leak|hiss)|\bsmell\w*\b.{0,12}\bgas\b|\b(battery|charger|power ?bank)\b.{0,30}\b(hot|warm|leaking)\b"]
SECURITY = [r"\b(hacked|breached|compromised|stolen|leaked|phishing|ransomware|malware)\b", r"\b(we|i|someone) (have |has |had )?(been )?(breached|hacked)\b", r"\bunauthori[sz]ed (access|login|charges?|transactions?|activity)\b", r"\bsomeone (else )?(logged|signed|got|broke) into\b",
            r"\b(account|password) (was |has been )?(taken over|changed by someone)\b", r"\b(data|information|details) (was |were |has been |have been )?(exposed|leaked|visible to (others|other customers|the public))\b", r"\bsuspicious (login|activity|charges?|emails?)\b",
            r"\bcan see (other|another) (customers?|users?|people)", r"\b(found|discovered|noticed|reporting) (a |an )?(security )?(vulnerability|exploit|hole|flaw)\b", r"\bsecurity incident\b"]
OUTAGE = [r"\b(there is|there's|we are having|we're having|is there|currently (an|having an)) (an )?outage\b", r"\b(site|service|app|system|platform|api|dashboard|website|everything) (is |has been |was |went )?(completely )?(down|offline|unavailable)\b",
          r"\bnot (working|loading|responding) for (anyone|everyone|all|any of|the whole|my whole)", r"\b(everyone|all of (us|our \w+|my \w+)|my whole team|our whole team) (cannot|can't|is unable|are unable|get(s)? an? error)",
          r"\b(500|502|503|504) (error|errors)\b", r"\bwhole (system|site|platform|api) (is )?(broken|failing|down)\b"]
CALM = [r"\bno rush\b", r"\bnot urgent\b", r"\bwhenever you (get|have) (a chance|time)\b", r"\bjust (curious|wondering)\b", r"\bwhen you (get|have) (a chance|time)\b", r"\bin the future\b", r"\bsomeday\b"]
PRODUCT_TERMS = {"link", "invite", "tier", "cards", "pay", "paying", "teammates", "colleague", "role", "charge", "cost", "year", "yearly", "monthly", "file", "files", "upload", "restore", "delete", "deleted", "okta", "two", "step", "backup", "offline", "region", "europe", "limit", "token", "workspaces", "cofounder", "you", "workspace", "note", "notes", "plan", "plans", "billing", "invoice", "member", "members", "export", "import", "account", "fieldnote", "password", "subscription", "price", "pricing", "api", "integration", "app", "feature",
                 "support", "team", "business", "free", "search", "template", "guest", "login", "sign", "sso", "payment", "card", "cancel", "refund", "trash", "version", "webhook", "slack", "calendar", "mobile", "desktop", "data", "privacy", "security"}
STRONG_NEG = {"furious", "useless", "terrible", "awful", "unacceptable", "ridiculous", "joke", "worst", "angry", "disgusting", "rude", "lawyer", "sue", "never again", "leaving", "fix it now", "fed up", "pathetic", "horrible", "bad"}
URGENT = [r"locked out", r"cannot (log|sign) ?in", r"can't (log|sign) ?in", r"\bdown\b", r"\boutage\b", r"lawyer", r"\bsue\b", r"\burgent\b", r"\basap\b", r"right now", r"meeting", r"\btoday\b", r"data (is )?gone"]
CATEGORIES = {"billing": r"\b(bill|billing|invoice|receipt|payment|card|charge|charged|price|pricing|plan|refund|subscription|cancel|renew)\w*", "account": r"\b(password|login|log in|sign in|two step|2fa|account|invite|member|role|owner|guest)\w*",
              "how_to": r"\b(how (do|can|to)|where|steps|export|import|search|template|history|shortcut)\b", "integration": r"\b(api|webhook|slack|calendar|zapier|token|integration)\w*", "security_privacy": r"\b(security|privacy|gdpr|encrypt|data residency|sso|saml|personal data)\w*",
              "outage": r"\b(down|outage|not working|broken|error|bug|crash)\w*"}
LANG_WORDS = {"es": {"hola", "cómo", "puedo", "mis", "todas", "notas", "gracias", "por", "favor", "quiero", "tengo", "para", "el", "los", "las", "una", "está", "botón"},
              "de": {"guten", "tag", "wie", "lange", "werden", "gelöschte", "notizen", "aufbewahrt", "ich", "und", "der", "die", "das", "nicht", "bitte", "kann", "haben", "wir"},
              "fr": {"bonjour", "comment", "puis", "je", "inviter", "un", "collègue", "dans", "mon", "espace", "de", "travail", "merci", "je", "pour", "les", "vous", "est"},
              "pt": {"olá", "qual", "é", "o", "preço", "do", "plano", "por", "mês", "obrigado", "como", "posso", "uma", "para", "não"},
              "it": {"buongiorno", "posso", "avere", "un", "rimborso", "per", "il", "piano", "annuale", "grazie", "come", "della", "non", "che"}}
EN_COMMON = {"the", "is", "how", "can", "i", "my", "to", "a", "and", "do", "you", "we", "it", "of", "what", "please", "have", "on", "are", "in", "for", "that"}


@dataclass
class Triage:
    category: str = "other"
    urgency: str = "normal"
    sentiment: str = "neutral"
    language: str = "en"
    flags: list[str] = field(default_factory=list)
    cleaned: str = ""  # the question with injection sentences removed


def detect_language(text: str) -> str:
    ws = [w.lower() for w in re.findall(r"[^\W\d_]+", text)]
    if not ws:
        return "en"
    en = sum(1 for w in ws if w in EN_COMMON)
    best, score = "en", 0
    for lang, vocab in LANG_WORDS.items():
        n = sum(1 for w in ws if w in vocab and w not in EN_COMMON)
        if n > score:
            best, score = lang, n
    if re.search(r"[¿¡ßãõçñ]", text) and score >= 1:
        return best
    return best if score >= 2 and score > en else "en"


def find_injection(text: str) -> list[str]:
    return [p for p in INJECTION if re.search(p, text, re.I | re.S)]


def strip_injection(text: str) -> str:
    """Remove the sentences that try to steer the system, so only the real question is searched."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    parts = re.split(r"(?<=[.!?])\s+", text)
    keep = [p for p in parts if not any(re.search(pat, p, re.I) for pat in INJECTION) and not re.search(r"\bapprove refund\b", p, re.I)]
    return " ".join(keep).strip()


def sentiment_of(text: str) -> str:
    low = text.lower()
    caps = sum(1 for w in re.findall(r"[A-Za-z]{4,}", text) if w.isupper())
    score = sum(1 for w in STRONG_NEG if w in low) + (1 if text.count("!") >= 2 else 0) + (1 if caps >= 2 else 0)
    if score >= 2:
        return "angry"
    if score == 1 or re.search(r"\b(disappointed|frustrated|annoyed|not happy)\b", low):
        return "negative"
    return "positive" if re.search(r"\b(thanks|thank you|great|love)\b", low) else "neutral"


def triage(subject: str, body: str, requester_email: str = "", plan: str = "free") -> Triage:
    text = f"{subject}. {body}"
    t = Triage(language=detect_language(text))
    low = text.lower()
    if find_injection(text):
        t.flags.append("injection")
    t.cleaned = strip_injection(f"{subject}. {body}") if "injection" in t.flags else text
    low = t.cleaned.lower()
    if any(re.search(p, low, re.I) for p in PRIVACY):
        t.flags.append("other_customer_data")
    elif re.search(r"@[\w.-]+\.\w+", body) and re.search(r"\b(phone|number|card|password|address|on file)\b", low):
        other = [e for e in re.findall(r"[\w.+-]+@[\w.-]+\.\w+", body) if e.lower() != requester_email.lower()]
        if other:
            t.flags.append("other_customer_data")
    if any(re.search(p, low, re.I) for p in OUT_OF_SCOPE):
        t.flags.append("out_of_scope")
    elif not (set(w.lower() for w in words(t.cleaned)) & PRODUCT_TERMS):
        t.flags.append("no_product_terms")  # soft: only becomes a refusal when the knowledge base has nothing either
    if REFUND_REQUEST.search(low) and ((not POLICY_QUESTION.search(low) and not HYPOTHETICAL.search(low)) or re.search(r"(please refund|refund me|refund my)", low)):
        t.flags.append("refund_request")
    if re.search(r"\b(lawyer|sue you|sue\b|manager|rude to me|tell everyone|never again|legal action|speak to someone senior)\b", low):
        t.flags.append("needs_person")
    for flag, pats in (("legal", LEGAL), ("safety", SAFETY), ("security", SECURITY), ("outage", OUTAGE)):
        if any(re.search(p, low, re.I) for p in pats):
            t.flags.append(flag)
    if "legal" in t.flags and "needs_person" not in t.flags:
        t.flags.append("needs_person")
    if "injection" in t.flags and len(words(strip_injection(body))) < 3:
        t.flags.append("injection_only")  # after the orders are removed no question is left
    t.sentiment = sentiment_of(text)
    if t.sentiment == "angry":
        t.flags.append("angry")
    counts = {c: len(re.findall(p, low)) for c, p in CATEGORIES.items()}
    best = max(counts, key=counts.get)
    t.category = "billing" if "refund_request" in t.flags else (best if counts[best] else "other")
    urgent_hits = sum(1 for p in URGENT if re.search(p, low))
    calm = any(re.search(p, low) for p in CALM)
    incident = bool({"safety", "security", "outage"} & set(t.flags))
    t.urgency = ("urgent" if urgent_hits >= 2 or incident or "legal" in t.flags else "high" if (urgent_hits or t.sentiment == "angry" or plan == "business") and not calm
                 else "low" if (t.sentiment == "positive" and plan == "free") or calm else "normal")
    return t
