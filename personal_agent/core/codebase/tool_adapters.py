from __future__ import annotations

from pathlib import Path
from typing import Any

from .controlled_patch import apply_patch, read_style_profile, run_allowed_command, validate_patch
from .impact_analyzer import analyze_codebase_impact
from .patch_planner import propose_patch
from .retriever import (
    build_codebase_index,
    call_graph_query,
    codebase_search,
    include_impact_query,
    macro_impact_query,
    symbol_lookup,
    type_usage_query,
    variable_usage_query,
)


def execute_codebase_tool(db_path: Path, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    project_id = int(tool_input.get("project_id") or 0)
    if tool_name == "codebase_index":
        return build_codebase_index(
            db_path,
            project_id,
            repo_path=str(tool_input.get("repo_path") or ""),
            query=str(tool_input.get("query") or ""),
            max_files=int(tool_input.get("max_files") or 160),
            skip_dirs=[str(item) for item in (tool_input.get("skip_dirs") or [])],
            batch_size=int(tool_input.get("batch_size") or 0),
        )
    if tool_name == "codebase_search":
        return codebase_search(db_path, project_id, str(tool_input.get("query") or ""), limit=int(tool_input.get("limit") or 8))
    if tool_name == "symbol_lookup":
        return symbol_lookup(
            db_path,
            project_id,
            str(tool_input.get("name") or ""),
            kind=str(tool_input.get("kind") or ""),
            limit=int(tool_input.get("limit") or 20),
        )
    if tool_name == "include_impact_query":
        return include_impact_query(db_path, project_id, str(tool_input.get("path") or ""))
    if tool_name == "call_graph_query":
        return call_graph_query(db_path, project_id, str(tool_input.get("function_name") or ""), limit=int(tool_input.get("limit") or 20))
    if tool_name == "macro_impact_query":
        return macro_impact_query(db_path, project_id, str(tool_input.get("macro_name") or ""), limit=int(tool_input.get("limit") or 20))
    if tool_name == "type_usage_query":
        return type_usage_query(db_path, project_id, str(tool_input.get("type_name") or ""), limit=int(tool_input.get("limit") or 20))
    if tool_name == "variable_usage_query":
        return variable_usage_query(db_path, project_id, str(tool_input.get("variable_name") or ""), limit=int(tool_input.get("limit") or 20))
    if tool_name == "impact_analyze":
        return analyze_codebase_impact(db_path, project_id, str(tool_input.get("change_hint") or ""), limit=int(tool_input.get("limit") or 8))
    if tool_name == "style_profile_read":
        return read_style_profile(db_path, project_id)
    if tool_name == "patch_propose":
        return propose_patch(db_path, project_id, tool_input)
    if tool_name == "patch_validate":
        return validate_patch(db_path, project_id, tool_input)
    if tool_name == "patch_apply":
        return apply_patch(db_path, project_id, tool_input)
    if tool_name in {"run_build", "run_tests", "run_static_analysis"}:
        return run_allowed_command(db_path, project_id, tool_name, tool_input)
    raise ValueError(f"Unknown codebase tool: {tool_name}")
