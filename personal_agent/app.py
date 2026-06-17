from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .bootstrap import PersonalAgentContext, bootstrap_personal_agent, load_personal_env
from .routes import register_personal_agent_routes
from .runtime import PersonalRuntime


def create_personal_app(db_path: Path, workspace: Path | None = None) -> FastAPI:
    workspace_path = workspace or Path.cwd()
    load_personal_env(workspace_path.expanduser().resolve() / ".env")
    context = bootstrap_personal_agent(db_path, workspace_path)
    runtime = PersonalRuntime(context.db_path, context.workspace, context.project_id)
    app = FastAPI(title="Personal Local Agent")
    register_personal_agent_routes(app, context=context, runtime=runtime)
    return app


def serve(db_path: Path, workspace: Path | None = None, port: int = 7870) -> None:
    uvicorn.run(create_personal_app(db_path, workspace), host="127.0.0.1", port=port)
