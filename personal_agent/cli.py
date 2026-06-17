from __future__ import annotations

import argparse
import os
from pathlib import Path

from .app import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--db", default=".personal_agent/agent.db")
    serve_parser.add_argument("--workspace", default=".")
    serve_parser.add_argument("--port", type=int, default=7870)
    serve_parser.add_argument("--fake-llm", action="store_true", help="Use the built-in fake LLM provider for local smoke tests.")

    args = parser.parse_args(argv)
    if args.command == "serve":
        if args.fake_llm:
            os.environ.setdefault("PERSONAL_AGENT_LLM_PROVIDER", "fake")
        serve(Path(args.db), Path(args.workspace), args.port)
        return 0
    return 2
