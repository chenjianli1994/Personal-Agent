from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .codebase.tool_adapters import execute_codebase_tool
from .database import connect
from .utils import json_dumps, utc_now


ToolRiskLevel = Literal["read_only", "write_candidate", "write_project", "external_side_effect"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission_scope: str
    side_effect_level: str
    timeout_s: int
    rollback_behavior: str
    evidence_output: list[str] = field(default_factory=list)
    allowed_callers: list[str] = field(default_factory=list)
    requires_confirmation: bool = False
    risk_level: ToolRiskLevel = "read_only"
    supports_dry_run: bool = False
    timeout_seconds: int = 30


def default_tool_specs() -> dict[str, ToolSpec]:
    specs = [
        ToolSpec("codebase_index", "Build or refresh the personal codebase index.", {"required": ["project_id"]}, {"fields": ["exists"]}, "codebase.read", "read", 30, "not_needed", ["code_files", "code_symbols"], ["conversation"]),
        ToolSpec("codebase_search", "Search indexed code files and symbols.", {"required": ["project_id", "query"]}, {"fields": ["file_results", "symbol_results"]}, "codebase.read", "read", 10, "not_needed", ["code_files", "code_symbols"], ["conversation"]),
        ToolSpec("symbol_lookup", "Look up indexed code symbols.", {"required": ["project_id", "name"]}, {"fields": ["matches"]}, "codebase.read", "read", 10, "not_needed", ["code_symbols"], ["conversation"]),
        ToolSpec("include_impact_query", "Read include impact.", {"required": ["project_id", "path"]}, {"fields": ["affected_files"]}, "codebase.read", "read", 10, "not_needed", ["code_files"], ["conversation"]),
        ToolSpec("call_graph_query", "Read callers and callees.", {"required": ["project_id", "function_name"]}, {"fields": ["callers", "callees"]}, "codebase.read", "read", 10, "not_needed", ["code_symbols"], ["conversation"]),
        ToolSpec("macro_impact_query", "Read macro impacts.", {"required": ["project_id", "macro_name"]}, {"fields": ["conditional_blocks"]}, "codebase.read", "read", 10, "not_needed", ["code_symbols"], ["conversation"]),
        ToolSpec("type_usage_query", "Read type usage.", {"required": ["project_id", "type_name"]}, {"fields": ["definitions"]}, "codebase.read", "read", 10, "not_needed", ["code_symbols"], ["conversation"]),
        ToolSpec("variable_usage_query", "Read variable usage.", {"required": ["project_id", "variable_name"]}, {"fields": ["references"]}, "codebase.read", "read", 10, "not_needed", ["code_symbols"], ["conversation"]),
        ToolSpec("impact_analyze", "Produce a structured code impact summary.", {"required": ["project_id", "change_hint"]}, {"fields": ["affected_files"]}, "codebase.read", "read", 15, "not_needed", ["code_files", "code_symbols"], ["conversation"]),
        ToolSpec("style_profile_read", "Read code style profile.", {"required": ["project_id"]}, {"fields": ["profile"]}, "codebase.read", "read", 5, "not_needed", ["style_profile_id"], ["conversation"]),
        ToolSpec("patch_propose", "Generate a candidate patch without writing project files.", {"required": ["project_id", "change_text"]}, {"fields": ["patch_text", "artifact"]}, "codebase.patch_candidate", "write", 20, "candidate drafts only", ["code_files", "code_symbols"], ["conversation"], False, "write_candidate", True),
        ToolSpec("patch_validate", "Validate a candidate diff.", {"required": ["project_id"]}, {"fields": ["passed"]}, "codebase.patch_candidate", "validation", 20, "not_needed", ["code_files"], ["conversation"], False, "read_only", True),
        ToolSpec("patch_apply", "Apply a reviewed candidate diff after confirmation.", {"required": ["project_id"]}, {"fields": ["passed", "modified_files"]}, "codebase.write", "write", 20, "manual rollback", ["code_files"], ["conversation"], True, "write_project", True),
        ToolSpec("run_build", "Run the configured build command.", {"required": ["project_id"]}, {"fields": ["passed", "command"]}, "toolchain.run", "external", 300, "not_needed", ["validation"], ["conversation"], True, "external_side_effect"),
        ToolSpec("run_tests", "Run the configured test command.", {"required": ["project_id"]}, {"fields": ["passed", "command"]}, "toolchain.run", "external", 300, "not_needed", ["validation"], ["conversation"], True, "external_side_effect"),
        ToolSpec("run_static_analysis", "Run the configured static analysis command.", {"required": ["project_id"]}, {"fields": ["passed", "command"]}, "toolchain.run", "external", 300, "not_needed", ["validation"], ["conversation"], True, "external_side_effect"),
    ]
    return {item.name: item for item in specs}


class TypedToolExecutor:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.specs = default_tool_specs()

    def invoke(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        task_uid: str = "",
        decision_uid: str = "",
        project_id: int | None = None,
        requirement_id: str = "",
        caller: str = "conversation",
        confirmed: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        effective_project_id = self._audit_project_id(project_id, tool_input)
        spec = self.specs.get(tool_name)
        if not spec:
            return self._record(tool_name, tool_input, {}, "failed", f"Unknown tool: {tool_name}", task_uid, decision_uid, effective_project_id, requirement_id, {}, "unknown", "read_only", dry_run, confirmed)
        error = self._validate(spec, tool_input, caller, confirmed, dry_run)
        if error:
            return self._record(tool_name, tool_input, {}, "rejected", error, task_uid, decision_uid, effective_project_id, requirement_id, {}, spec.side_effect_level, spec.risk_level, dry_run, confirmed)
        try:
            effective_input = {**tool_input, "dry_run": True} if dry_run and spec.supports_dry_run else tool_input
            output = execute_codebase_tool(self.db_path, spec.name, effective_input)
            return self._record(tool_name, tool_input, output, "ok", "", task_uid, decision_uid, effective_project_id, requirement_id, output.get("evidence_refs", {}), spec.side_effect_level, spec.risk_level, dry_run, confirmed)
        except Exception as exc:
            return self._record(tool_name, tool_input, {}, "failed", str(exc), task_uid, decision_uid, effective_project_id, requirement_id, {}, spec.side_effect_level, spec.risk_level, dry_run, confirmed)

    def _validate(self, spec: ToolSpec, tool_input: dict[str, Any], caller: str, confirmed: bool, dry_run: bool) -> str:
        if spec.allowed_callers and caller not in spec.allowed_callers:
            return f"Caller {caller} is not allowed to use {spec.name}"
        missing = [name for name in spec.input_schema.get("required", []) if name not in tool_input or _missing_tool_value(tool_input.get(name))]
        if missing:
            return f"Missing required tool input: {', '.join(missing)}"
        if dry_run and spec.risk_level != "read_only" and not spec.supports_dry_run:
            return f"Tool {spec.name} does not support dry_run"
        if not dry_run and spec.requires_confirmation and not confirmed:
            return f"Tool {spec.name} requires explicit confirmation"
        return ""

    def _audit_project_id(self, project_id: int | None, tool_input: dict[str, Any]) -> int | None:
        raw = project_id if project_id is not None else tool_input.get("project_id")
        if raw in {None, ""}:
            return None
        candidate = int(raw or 0)
        if candidate <= 0:
            return None
        with connect(self.db_path) as conn:
            exists = conn.execute("SELECT 1 FROM projects WHERE id=?", (candidate,)).fetchone()
        return candidate if exists else None

    def _record(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        output: dict[str, Any],
        status: str,
        error: str,
        task_uid: str,
        decision_uid: str,
        project_id: int | None,
        requirement_id: str,
        evidence_refs: dict[str, Any],
        side_effect_level: str,
        risk_level: str,
        dry_run: bool,
        confirmed: bool,
    ) -> dict[str, Any]:
        started = utc_now()
        invocation_uid = f"ptool_{started.replace('-', '').replace(':', '').replace('T', '_').replace('Z', '')}_{tool_name}_{uuid4().hex[:8]}"
        permission = {
            "tool_name": tool_name,
            "side_effect_level": side_effect_level,
            "risk_level": risk_level,
            "dry_run": dry_run,
            "confirmation": "confirmed" if confirmed else "not_confirmed",
        }
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO personal_tool_invocations(
                    invocation_uid, task_uid, decision_uid, project_id, requirement_id,
                    tool_name, input_json, output_json, permission_snapshot_json,
                    side_effect_level, status, error, evidence_refs_json, started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invocation_uid,
                    task_uid,
                    decision_uid,
                    project_id,
                    requirement_id,
                    tool_name,
                    json_dumps(tool_input),
                    json_dumps(output),
                    json_dumps(permission),
                    side_effect_level,
                    status,
                    error,
                    json_dumps(evidence_refs),
                    started,
                    utc_now(),
                ),
            )
        return {
            "invocation_uid": invocation_uid,
            "tool": tool_name,
            "tool_name": tool_name,
            "status": status,
            "error": error,
            "output": output,
            "permission_snapshot": permission,
            "side_effect_level": side_effect_level,
            "risk_level": risk_level,
            "dry_run": dry_run,
            "confirmation": permission["confirmation"],
        }


def _missing_tool_value(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, (list, dict, tuple, set)) and not value:
        return True
    return False
