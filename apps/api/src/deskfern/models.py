"""Deskfern tables (prefix lh_). They sit on the shared Base. Money columns end in `_minor` (integer cents)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sendora_core.db import Base, UTCDateTime, utcnow

TICKET_STATUSES = ("new", "drafted", "awaiting_approval", "escalated", "answered", "refused", "duplicate", "closed")
URGENCIES = ("low", "normal", "high", "urgent")


class State(Base):
    __tablename__ = "lh_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Customer(Base):
    __tablename__ = "lh_customer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(20), default="free")
    interval: Mapped[str] = mapped_column(String(10), default="none")
    seats: Mapped[int] = mapped_column(Integer, default=1)
    first_paid: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_paid: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    charge_id: Mapped[str] = mapped_column(String(60), default="")
    refunds_12m: Mapped[int] = mapped_column(Integer, default=0)
    flag: Mapped[str] = mapped_column(String(40), default="")  # billing facts from the payment system, e.g. duplicate_charge


class Source(Base):
    __tablename__ = "lh_source"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # folder, drive, notion, site
    name: Mapped[str] = mapped_column(String(120))
    uri: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    documents: Mapped[list[Document]] = relationship(back_populates="source", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "lh_document"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("lh_source.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(300), index=True)
    slug: Mapped[str] = mapped_column(String(120), default="", index=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(10), default="md")
    updated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    outdated: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[Source] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="Chunk.ordinal")


class Chunk(Base):
    __tablename__ = "lh_chunk"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("lh_document.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(300), default="")
    context: Mapped[str] = mapped_column(String(400), default="")  # short line that says where the chunk sits
    text: Mapped[str] = mapped_column(Text)
    start: Mapped[int] = mapped_column(Integer, default=0)  # character offsets inside the document text
    end: Mapped[int] = mapped_column(Integer, default=0)
    stems: Mapped[str] = mapped_column(Text, default="")  # stemmed words of context and text, what the full text index reads
    vec: Mapped[dict] = mapped_column(JSON, default=dict)  # hashed bag of words, NOT a language model embedding
    document: Mapped[Document] = relationship(back_populates="chunks")


class Ticket(Base):
    __tablename__ = "lh_ticket"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(20), default="seed")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("lh_customer.id"), nullable=True, index=True)
    requester_email: Mapped[str] = mapped_column(String(254), default="")
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    category: Mapped[str] = mapped_column(String(30), default="")
    urgency: Mapped[str] = mapped_column(String(10), default="normal")
    sentiment: Mapped[str] = mapped_column(String(10), default="neutral")
    language: Mapped[str] = mapped_column(String(8), default="en")
    flags: Mapped[list] = mapped_column(JSON, default=list)
    outcome: Mapped[str] = mapped_column(String(30), default="")  # answer, escalate, refuse, refund_request, duplicate, conflict_escalate
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("lh_ticket.id"), nullable=True)
    customer: Mapped[Customer | None] = relationship()
    messages: Mapped[list[Message]] = relationship(back_populates="ticket", cascade="all, delete-orphan", order_by="Message.id")
    answers: Mapped[list[Answer]] = relationship(back_populates="ticket", cascade="all, delete-orphan", order_by="Answer.id")


class Message(Base):
    __tablename__ = "lh_message"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("lh_ticket.id"), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # in, out, note
    author: Mapped[str] = mapped_column(String(254), default="")
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class Answer(Base):
    __tablename__ = "lh_answer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("lh_ticket.id"), index=True)
    decision: Mapped[str] = mapped_column(String(12))  # answer, escalate, refuse
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft; held (nothing left: draft mode or Pause all); sending (claimed, not confirmed); sent; edited (sent with the agent's edit); discarded
    text: Mapped[str] = mapped_column(Text, default="")
    sentences: Mapped[list] = mapped_column(JSON, default=list)  # {text, supported, support, chunk_ids, removed, reason}
    retrieval_strength: Mapped[float] = mapped_column(Float, default=0.0)
    groundedness: Mapped[float] = mapped_column(Float, default=0.0)
    policy_match: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    writer: Mapped[str] = mapped_column(String(40), default="offline rules")
    edited_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ticket: Mapped[Ticket] = relationship(back_populates="answers")
    citations: Mapped[list[Citation]] = relationship(back_populates="answer", cascade="all, delete-orphan")


class Citation(Base):
    __tablename__ = "lh_citation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("lh_answer.id"), index=True)
    sentence_index: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("lh_chunk.id"))
    quote: Mapped[str] = mapped_column(Text)  # the exact passage inside the chunk that supports the sentence
    support: Mapped[float] = mapped_column(Float, default=0.0)
    answer: Mapped[Answer] = relationship(back_populates="citations")
    chunk: Mapped[Chunk] = relationship()


class Escalation(Base):
    __tablename__ = "lh_escalation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("lh_ticket.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(60), default="support lead")
    status: Mapped[str] = mapped_column(String(12), default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PolicyRule(Base):
    __tablename__ = "lh_policy_rule"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")


class PolicyAction(Base):
    __tablename__ = "lh_policy_action"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("lh_ticket.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="refund")
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    charge_id: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested, approved, recorded_draft, executed, rejected
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rationale: Mapped[dict] = mapped_column(JSON, default=dict)
    external_id: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class HealthScore(Base):
    __tablename__ = "lh_health_score"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("lh_customer.id"), unique=True)
    score: Mapped[int] = mapped_column(Integer)
    risk: Mapped[str] = mapped_column(String(8))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer: Mapped[Customer] = relationship()


class Gap(Base):
    __tablename__ = "lh_gap"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    samples: Mapped[list] = mapped_column(JSON, default=list)
    ticket_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(10), default="open")


class TestCase(Base):
    __tablename__ = "lh_test_case"
    __test__ = False
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    expected_text: Mapped[str] = mapped_column(Text, default="")
    expected_docs: Mapped[list] = mapped_column(JSON, default=list)
    expected_facts: Mapped[list] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(20), default="agent_edit")
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_result: Mapped[str] = mapped_column(String(10), default="")  # pass, fail, ""
    last_detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CrawlJob(Base):
    __tablename__ = "lh_crawl_job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("lh_source.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="queued")
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class EvalRun(Base):
    __tablename__ = "lh_eval_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    held_out: Mapped[bool] = mapped_column(Boolean, default=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
