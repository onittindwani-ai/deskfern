"""Command line helpers for a real install. They use the same settings and the same functions as the app.

  python -m deskfern.admin import-customers customers.csv     add or update customers (plan and payment facts that refund answers read)
  python -m deskfern.admin sync-knowledge                      read the KNOWLEDGE_DIR folder into the knowledge base
  python -m deskfern.admin erase-customer person@example.com   erase one customer and their tickets on request (cannot be undone; see docs/11)
  python -m deskfern.admin lift-erasure person@example.com     accept messages and imports from an erased address again (nothing erased comes back)

ENV_FILE names another settings file than .env, as for the API and the worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from deskfern import importing, privacy
from deskfern.config import Settings
from deskfern.context import Ctx
from deskfern.dbsetup import make_private_engine
from sendora_core.db import Base, make_session_factory, session_scope


def _ctx(settings: Settings) -> Ctx:
    import deskfern.models  # noqa: F401
    from sendora_core import models as _c  # noqa: F401

    engine = make_private_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)  # Postgres is set up with `alembic upgrade head`
    ctx = Ctx(settings, make_session_factory(engine))
    privacy.ensure_install_values(ctx.factory)
    return ctx


def run(argv: list[str], settings: Settings | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m deskfern.admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("import-customers", help="add or update customers from a CSV file")
    c.add_argument("file")
    sub.add_parser("sync-knowledge", help="read the KNOWLEDGE_DIR folder")
    e = sub.add_parser("erase-customer", help="erase one customer and their tickets on request (cannot be undone)")
    e.add_argument("email")
    lift = sub.add_parser("lift-erasure", help="accept messages and imports from an erased address again")
    lift.add_argument("email")
    args = ap.parse_args(argv)
    settings = settings or Settings(_env_file=os.environ.get("ENV_FILE", ".env"))
    settings.check_runtime()
    ctx = _ctx(settings)
    try:
        with session_scope(ctx.factory) as s:
            if args.cmd == "import-customers":
                report = importing.import_customers(s, Path(args.file).read_text(encoding="utf-8-sig"))
            elif args.cmd == "erase-customer":
                report = privacy.erase_customer(s, args.email, "command line")
            elif args.cmd == "lift-erasure":
                report = privacy.lift_erasure(s, args.email, "command line")
            else:
                report = importing.sync_knowledge_folder(s, ctx)
    except (importing.ImportProblem, privacy.EraseBlocked, OSError) as exc:
        print(f"Nothing was changed. {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
