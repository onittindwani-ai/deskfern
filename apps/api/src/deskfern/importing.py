"""What a real install needs that the demo seed used to provide: the customers (their plan and payment facts, which refund answers read) and the
knowledge folder. Both are reachable from the app (owner routes) and from the command line (`python -m deskfern.admin`)."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deskfern import ingest, policies, privacy
from deskfern.models import Customer, Source
from deskfern.util import clean_text, is_valid_email, normalize_email

MAX_CSV_CHARS = 2_000_000
MAX_ROWS = 5000
INTERVALS = {"monthly", "annual", "none"}
COLUMNS = ("email", "name", "plan", "interval", "seats", "first_paid", "last_paid", "amount_minor", "charge_id", "refunds_12m", "flag", "key")


class ImportProblem(ValueError):
    """The whole file cannot be read (too big, empty, or no email column). The message is plain words for the person importing."""


def _int(value: str, name: str, low: int = 0, high: int = 10**9) -> int:
    try:
        n = int(value.strip() or 0)
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None
    if not low <= n <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return n


def _date(value: str, name: str) -> date | None:
    v = value.strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{name} must be a date like 2026-11-30") from None


def parse_customer(row: dict[str, str]) -> dict:
    """One CSV row to the fields of a Customer. Raises ValueError with a plain reason."""
    email = normalize_email(row.get("email", ""))
    if not is_valid_email(email):
        raise ValueError("email is missing or not an address")
    plan = clean_text(row.get("plan", ""), 20).strip().lower() or "free"
    interval = clean_text(row.get("interval", ""), 10).strip().lower() or ("none" if plan == "free" else "monthly")
    if interval not in INTERVALS:
        raise ValueError("interval must be monthly, annual or none")
    amount = _int(row.get("amount_minor", ""), "amount_minor")
    charge = clean_text(row.get("charge_id", ""), 60).strip()
    flag = clean_text(row.get("flag", ""), 40).strip()
    if flag and flag not in policies.FLAG_TEXT:
        raise ValueError(f"flag must be empty or one of: {', '.join(policies.FLAG_TEXT)}")
    last_paid = _date(row.get("last_paid", ""), "last_paid")
    if plan != "free" and amount and not charge:
        raise ValueError("charge_id is needed for a paying customer, so a refund can name the charge")
    return {"email": email, "name": clean_text(row.get("name", ""), 160).strip() or email.split("@")[0], "plan": plan, "interval": interval, "seats": _int(row.get("seats", ""), "seats", 0, 100000) or 1,
            "first_paid": _date(row.get("first_paid", ""), "first_paid") or last_paid, "last_paid": last_paid, "amount_minor": amount, "charge_id": charge,
            "refunds_12m": _int(row.get("refunds_12m", ""), "refunds_12m", 0, 1000), "flag": flag, "key": clean_text(row.get("key", ""), 20).strip()}


def import_customers(session: Session, text: str) -> dict:
    """Create or update customers from CSV text (header row: email is required; name, plan, interval, seats, first_paid, last_paid, amount_minor,
    charge_id, refunds_12m, flag and key are optional). A customer is matched by email. A bad row is reported and skipped, the rest are saved."""
    if len(text) > MAX_CSV_CHARS:
        raise ImportProblem(f"The file is too large (over {MAX_CSV_CHARS:,} characters). Split it into smaller files.")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    names = [(n or "").strip().lower() for n in (reader.fieldnames or [])]
    if "email" not in names:
        raise ImportProblem("The first line must name the columns, and one of them must be email. The columns are: " + ", ".join(COLUMNS) + ".")
    reader.fieldnames = names
    report = {"created": 0, "updated": 0, "skipped": 0, "errors": [], "ignored_columns": [n for n in names if n and n not in COLUMNS]}
    seen: set[str] = set()
    present = set(names)  # an existing customer is changed only in the columns the file has: a file without refunds_12m keeps the number the desk already knows
    for i, raw in enumerate(reader, start=2):  # line 1 is the header
        if i - 1 > MAX_ROWS:
            raise ImportProblem(f"More than {MAX_ROWS} rows. Split the file and import it in parts.")
        row = {k: (v or "") for k, v in raw.items() if k}
        try:
            data = parse_customer(row)
            if data["email"] in seen:
                raise ValueError("this email appears twice in the file")
            seen.add(data["email"])
            if privacy.is_erased(session, data["email"]):
                raise ValueError("this address was erased on request and is not added again")
        except ValueError as exc:
            report["skipped"] += 1
            report["errors"].append({"line": i, "why": str(exc)})
            continue
        cust = session.scalar(select(Customer).where(func.lower(Customer.email) == data["email"]))
        key = data.pop("key") or (cust.key if cust else "c-" + hashlib.sha256(data["email"].encode()).hexdigest()[:10])
        if cust is None:
            if session.scalar(select(Customer.id).where(Customer.key == key)):
                report["skipped"] += 1
                report["errors"].append({"line": i, "why": "this key is already used by another customer"})
                continue
            session.add(Customer(key=key, **data))
            report["created"] += 1
        else:
            for k, v in data.items():
                if k in present:
                    setattr(cust, k, v)
            report["updated"] += 1
    session.flush()
    return report


def sync_knowledge_folder(session: Session, ctx) -> dict:
    """Read the folder named by KNOWLEDGE_DIR (markdown, text and PDF files) into the knowledge base. Safe to repeat: unchanged files stay as they are."""
    if not ctx.settings.knowledge_dir:
        raise ImportProblem("KNOWLEDGE_DIR is not set. Set it to the folder that holds your help pages (see docs/04-ENVIRONMENT-VARIABLES.md).")
    folder = Path(ctx.settings.knowledge_dir)
    if not folder.is_dir():
        raise ImportProblem("KNOWLEDGE_DIR does not point to a folder the desk can read. Check the name and that the folder is mounted.")
    src = session.scalar(select(Source).where(Source.kind == "folder", Source.uri == "knowledge folder"))
    if src is None:
        src = Source(kind="folder", name="Help centre", uri="knowledge folder", status="new")
        session.add(src)
        session.flush()
    counts = ingest.ingest_folder(session, src, folder, ctx.today(session))
    src.status, src.error, src.last_synced_at = "ready", "", ctx.now(session)
    return counts
