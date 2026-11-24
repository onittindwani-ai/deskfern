"""Words people use for the same thing as the help pages. Built by hand from the failures of the seed tickets (tuned on the seed, not on the held out set, which was run once before this file existed).
Each word is stemmed with the same stemmer as the index and adds extra search words. It is an offline stand in for what a language model embedding would do."""

from deskfern.textproc import stem

RAW = {
    "colleague": ["member", "invite"], "cofounder": ["member", "invite"], "teammate": ["member"], "coworker": ["member"], "employee": ["member"], "staff": ["member"], "people": ["member"], "person": ["member"],
    "add": ["invite", "member"], "join": ["invite", "member"], "expired": ["work", "days"], "expire": ["work", "days"], "live": ["work", "days"],
    "cost": ["price", "dollars"], "price": ["cost", "dollars"], "charge": ["price", "billing", "dollars"], "head": ["member"], "tier": ["plan"], "middle": ["team"], "top": ["business"], "yearly": ["year", "annual"], "annual": ["year"], "pay": ["billing", "payment"],
    "paying": ["billing", "payment", "cancel"], "payment": ["billing", "receipt", "invoice"], "confirmations": ["receipt", "invoice"], "bookkeeper": ["invoice", "receipt", "billing"], "accountant": ["invoice", "receipt", "billing"],
    "stop": ["cancel"], "quit": ["cancel"], "leave": ["cancel"], "freeze": ["pause"], "paused": ["pause"],
    "bounced": ["failed", "declined", "payment", "retry"], "declined": ["failed", "payment", "retry"], "locked": ["read", "only", "failed"], "amex": ["american", "express"], "cards": ["card"],
    "backup": ["export"], "copy": ["export"], "download": ["export"], "everything": ["export", "every"], "wiped": ["deleted", "trash", "restore"], "removed": ["deleted", "trash", "restore"], "deleted": ["trash", "restore"], "vanish": ["trash", "end", "period"],
    "okta": ["sso", "saml", "single", "sign"], "login": ["sign", "password"], "log": ["sign", "password"], "authenticator": ["two", "step", "sign"], "2fa": ["two", "step", "sign"], "codes": ["two", "step"], "forgot": ["password", "reset"],
    "biggest": ["largest", "max", "limit"], "largest": ["limit"], "big": ["largest", "limit"], "hammer": ["rate", "limits", "requests"], "fast": ["rate", "limit"], "endpoint": ["webhook"], "events": ["webhook"], "event": ["webhook"],
    "train": ["offline"], "plane": ["offline"], "signal": ["offline", "connection"], "europe": ["european", "union"], "german": ["european", "union"], "germany": ["european", "union"], "keep": ["kept"], "kept": ["keep"],
    "role": ["owner", "admin"], "pass": ["transfer", "hand"], "hand": ["transfer"], "client": ["guest"], "outside": ["guest"], "clients": ["guest"], "sign": ["login"], "reset": ["password"],
    "outage": ["credit", "hours", "down"], "downtime": ["credit", "outage", "hours"], "down": ["credit", "outage"], "wiki": ["import", "markdown"], "folder": ["import", "markdown"], "files": ["import", "markdown"], "mobile": ["iphone", "android"], "iphone": ["offline", "mobile"],
    "reimburse": ["refund"], "reimbursement": ["refund"], "fee": ["price", "cost"], "fees": ["price", "cost"], "terminate": ["cancel"], "ship": ["shipping", "delivery"], "arrive": ["delivery", "shipping"], "wait": ["days", "time"],
    "duration": ["long", "days"], "defective": ["damaged", "broken"], "damaged": ["defective", "broken"], "guarantee": ["warranty"], "warranty": ["guarantee"], "discount": ["price", "coupon"], "coupon": ["discount", "code"], "replace": ["replacement", "exchange"],
    "exchange": ["replace", "replacement"], "swap": ["exchange", "replace"], "restrict": ["limit"], "restriction": ["limit"], "quota": ["limit"], "allowance": ["limit"], "hours": ["opening", "open"], "timeframe": ["days", "window"], "deadline": ["window", "days"],
    "call": ["phone"], "talk": ["phone"], "audit": ["security"], "auditor": ["security"], "slack": ["integration"], "encrypted": ["encryption"], "calls": ["requests", "api"], "api": ["token", "requests"],
}
EXPAND: dict[str, list[str]] = {stem(k): [stem(v) for v in vs] for k, vs in RAW.items()}


def expand(stems_: list[str]) -> list[str]:
    out = list(stems_)
    for w in stems_:
        for x in EXPAND.get(w, []):
            if x not in out:
                out.append(x)
    return out
