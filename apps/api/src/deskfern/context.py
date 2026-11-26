"""Runtime context shared by the API and the worker: settings, database, adapters, writer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from deskfern import clock, model_tasks
from deskfern.config import Settings
from deskfern.models import State
from deskfern.offline_llm import OfflineBackend
from deskfern.registry import AdapterSet, build_adapters, build_registry
from sendora_core.costs import CostTracker
from sendora_core.llm import LLM, build_llm
from sendora_core.settings import Mode


@dataclass
class Ctx:
    settings: Settings
    factory: sessionmaker[Session]
    adapters: AdapterSet | Any = None
    registry: Any = None
    writer: Any = None  # a Writer for answers; None means the offline rules writer
    crawler_factory: Any = None  # a function (ctx) returning a Crawler; None builds the real one from the settings (tests give it a stub site)

    def __post_init__(self) -> None:
        self.registry = self.registry or build_registry(self.settings)
        if self.adapters is None:
            self.adapters = build_adapters(self.settings, self.registry)

    def effective(self, session: Session) -> Settings:
        """The kill switch can be turned on in the app, and the mode can be pulled back to draft. Neither can go the other way."""
        kill, force = session.get(State, "kill_switch"), session.get(State, "force_draft")
        upd: dict[str, Any] = {}
        if kill and kill.value == "1":
            upd["kill_switch"] = True
        if force and force.value == "1":
            upd["mode"] = Mode.DRAFT
        return self.settings.model_copy(update=upd) if upd else self.settings

    def now(self, session: Session):
        return clock.now(session, self.settings.demo_shortcuts)

    def today(self, session: Session) -> date:
        return clock.today(session, self.settings.demo_shortcuts)

    def llm(self, session: Session) -> LLM:
        # LLM_BACKEND=offline (default) | fixture | anthropic. The offline stand in is the zero account default.
        # The anthropic path is UNVERIFIED against the real service. The model writer behind it comes in the next step.
        return build_llm(self.effective(session), CostTracker(session), offline=OfflineBackend())


    def answer_writer(self, session: Session):
        """The writer of reply sentences: the model behind the wrapper (the extractive stand in when LLM_BACKEND is offline). The groundedness check runs on its output either way."""
        return model_tasks.ModelWriter(self.llm(session))

    def judge(self, session: Session):
        """A sentence judge for the gray band, or None when JUDGE is rules (the default)."""
        if self.settings.judge != "model":
            return None
        llm = self.llm(session)
        return lambda sentence, chunks: model_tasks.judge_sentence(llm, sentence, chunks)

    def assist(self, session: Session, subject: str, body: str, tri):
        """The ticket triage after the model's second look when TRIAGE_ASSIST is model (it can only add caution), otherwise the rules' result as it is."""
        return model_tasks.assist(self.llm(session), subject, body, tri) if self.settings.triage_assist == "model" else tri


def set_state(session: Session, key: str, value: str) -> None:
    row = session.get(State, key)
    if row is None:
        session.add(State(key=key, value=value))
    else:
        row.value = value


def get_state(session: Session, key: str, default: str = "") -> str:
    row = session.get(State, key)
    return row.value if row else default
