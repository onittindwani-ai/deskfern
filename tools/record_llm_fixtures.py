"""Record the model answers for the seed tickets into data/llm_fixtures/, so the demo can replay them with LLM_BACKEND=fixture and no account.

IMPORTANT: these are NOT answers of a real model. The recording goes through the real anthropic code path (request building, schema, response parsing, the checks by code)
against a local mock server that answers with the offline stand in. So the fixtures prove the replay path and the request shape, not model quality. Replace them with a
recording from a real key (LLM_RECORD=true, LLM_BACKEND=anthropic) when you have one. Never commit recordings made from real customer data.

Run: python tools/record_llm_fixtures.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from deskfern import demo, model_tasks  # noqa: E402
from deskfern.config import Settings  # noqa: E402
from deskfern.context import Ctx  # noqa: E402
from deskfern.offline_llm import OfflineBackend  # noqa: E402
from mock.anthropic_mock import StubAnthropic  # noqa: E402
from sendora_core import models as _core  # noqa: E402,F401
from sendora_core.db import Base, make_engine, make_session_factory  # noqa: E402
from sendora_core.llm import reset_budgets  # noqa: E402

OUT = ROOT / "data" / "llm_fixtures"


def stand_in(body: dict) -> str:
    return OfflineBackend().complete(model=body["model"], system=body.get("system", ""), prompt=body["messages"][0]["content"], max_tokens=body["max_tokens"]).text


def record(out: Path = OUT, work: Path | None = None) -> dict:
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    if work is not None:
        Path(work).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp, StubAnthropic(stand_in) as stub:
        settings = Settings(database_url=f"sqlite:///{tmp}/rec.sqlite3", session_secret="record-secret-record-secret", llm_backend="anthropic", anthropic_api_key="stub-key-for-local-server",
                            anthropic_base_url=stub.url, llm_record=True, llm_fixtures_dir=str(out), _env_file=None)
        eng = make_engine(settings.database_url)
        Base.metadata.create_all(eng)
        ctx = Ctx(settings, make_session_factory(eng))
        reset_budgets()
        with ctx.factory() as s:
            info = demo.reset(s, ctx)
            model_tasks.suggest_page(ctx.llm(s), {"label": "invoice vat", "count": 3, "samples": ["Where do I find the VAT number on my invoice?"]})
            s.commit()
    files = len(list(out.glob("*.json")))
    (out / "README.txt").write_text(
        "Recorded from a LOCAL MOCK server that answers with the offline stand in. Not answers of a real model.\n"
        "They prove the replay path and the request shape only. Regenerate: python tools/record_llm_fixtures.py\n", encoding="utf-8")
    return {"tickets": info["seed"]["tickets"], "files": files, "requests_to_stub_server": len(stub.requests)}


if __name__ == "__main__":
    print(json.dumps(record()))
