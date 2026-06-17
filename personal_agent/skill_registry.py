from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from personal_agent.core.database import connect
from personal_agent.core.utils import json_dumps, read_text, utc_now


@dataclass(frozen=True)
class DefaultDocumentSkill:
    name: str
    display_name: str
    document_type: str
    description: str
    content_format: str
    instructions: str
    template_path: str = ""
    template_format: str = ""
    required_sections: tuple[str, ...] = ()


DEFAULT_DOCUMENT_SKILLS: tuple[DefaultDocumentSkill, ...] = (
    DefaultDocumentSkill(
        name="requirement-analysis-report",
        display_name="需求分析报告",
        document_type="requirement_analysis_report",
        description="将输入材料、项目知识和经验整理为需求分析报告，识别目标、边界、风险和验收线索。",
        content_format="markdown",
        instructions=(
            "生成 Markdown 需求分析报告。必须包含：输入摘要、原文事实表、术语与变量定义、需求理解、条件与状态机、"
            "歧义与待确认、关键假设、风险与边界、验收建议、证据引用。必须区分原文事实、分析推断和待确认问题。"
            "输入资料中出现变量、缩写、状态值、符号表达或阶段触发关系时，必须保留定义关系和阶段边界，不得直接摘要丢失。"
            "输入资料中出现 A/B、X/Y、T1/T2 等短变量或变更量时，必须抽取其物理含义、单位、适用对象和计算关系；无法确认时列入待确认，不得泛化替代。"
            "状态机必须区分进入条件、运行中条件、完成/退出触发和完成后处理，不得把运行中逻辑与完成后逻辑混写。"
            "不得擅自修正原始信号名、状态值、阈值、拼写或符号表达；疑似错误必须保留原文并列入待确认。"
            "不得引用未由输入资料、知识、记忆或代码证据提供的文件、接口、模块或代码实体。不得生成代码、补丁或发布记录。"
        ),
    ),
    DefaultDocumentSkill(
        name="requirement-breakdown",
        display_name="需求拆解",
        document_type="requirement_breakdown",
        description="将需求拆成可验证、单一职责的需求条目。",
        content_format="markdown",
        instructions=(
            "生成 Markdown 需求拆解文档。必须输出候选需求条目，每条包含 id、描述、触发条件、"
            "预期行为、验收标准、来源证据。不得混入实现代码。"
        ),
    ),
    DefaultDocumentSkill(
        name="functional-spec",
        display_name="功能规范",
        document_type="functional_spec",
        description="从用户可观察行为角度定义功能输入、输出、约束、异常和非目标。",
        content_format="markdown",
        instructions=(
            "生成 Markdown 功能规范。必须描述外部行为、输入输出、状态、异常场景、非目标和验收路径。"
            "只写用户可验证行为，不写函数实现、patch、diff、C 代码、伪代码或内部算法。"
        ),
    ),
    DefaultDocumentSkill(
        name="detailed-design",
        display_name="详细设计",
        document_type="detailed_design",
        description="基于需求和代码证据生成详细设计草稿，保留代码影响证据。",
        content_format="markdown",
        instructions=(
            "生成 Markdown 详细设计。必须包含模块职责、接口与数据、状态、错误处理、"
            "Codebase Impact 证据、Symbol Lookup 证据、Call Graph 证据、Include Impact 证据、"
            "Macro / Type / Variable 证据。可以描述设计方案，但不得生成可应用代码补丁。"
        ),
    ),
    DefaultDocumentSkill(
        name="test-case-spec",
        display_name="测试用例规格",
        document_type="test_case_spec",
        description="生成覆盖正常、边界、异常场景的测试用例规格表。",
        content_format="json_table",
        instructions=(
            "生成 JSON 表格，根对象必须包含 columns 和 rows。rows 至少覆盖 normal/正常、boundary/边界、"
            "exception/异常三类场景。只生成测试规格，不生成测试代码或 diff。"
        ),
    ),
)

DEFAULT_DOCUMENT_TYPES = {item.document_type for item in DEFAULT_DOCUMENT_SKILLS}

DEFAULT_TEMPLATE_SPECS: dict[str, dict[str, Any]] = {
    "requirement_analysis_report": {
        "path": "templates/default.md",
        "format": "markdown",
        "required_sections": ["输入摘要", "原文事实表", "术语与变量定义", "需求理解", "条件与状态机", "歧义与待确认", "关键假设", "风险与边界", "验收建议", "证据引用"],
        "content": "# 需求分析报告\n\n## 输入摘要\n{{input_summary}}\n\n## 原文事实表\n{{source_facts_table}}\n\n## 术语与变量定义\n{{term_and_variable_definitions}}\n\n> 若源文出现 A/B、X/Y、T1/T2 等短变量或变更量，本节必须保留其原始定义、物理含义、单位、适用对象、生效/采样时刻和计算关系；无法确认时列入“歧义与待确认”，不得用泛化摘要替代。建议列：符号、源文定义、物理含义、单位、适用对象、生效/采样时刻、计算关系、证据引用。\n\n## 需求理解\n{{requirement_understanding}}\n\n## 条件与状态机\n{{conditions_and_state_machine}}\n\n> 运行阶段表建议列：阶段、进入条件、运行中条件、适用对象、控制策略、退出/完成条件、完成后处理、证据引用。若源文同时描述进入状态和完成后动作，必须拆成独立阶段。\n\n## 歧义与待确认\n{{ambiguities_and_open_questions}}\n\n## 关键假设\n{{assumptions}}\n\n## 风险与边界\n{{risks_and_boundaries}}\n\n## 验收建议\n{{acceptance_suggestions}}\n\n## 证据引用\n{{evidence_refs}}\n",
    },
    "requirement_breakdown": {
        "path": "templates/default.md",
        "format": "markdown",
        "required_sections": ["候选需求条目", "触发条件", "预期行为", "验收标准", "证据引用"],
        "content": "# 需求拆解\n\n## 候选需求条目\n{{requirement_items}}\n\n## 触发条件\n{{triggers}}\n\n## 预期行为\n{{expected_behaviors}}\n\n## 验收标准\n{{acceptance_criteria}}\n\n## 证据引用\n{{evidence_refs}}\n",
    },
    "functional_spec": {
        "path": "templates/default.md",
        "format": "markdown",
        "required_sections": ["功能目标", "用户可观察行为", "输入与输出", "状态与异常场景", "非目标", "验收标准", "证据引用"],
        "content": "# 功能规范\n\n## 功能目标\n{{functional_goal}}\n\n## 用户可观察行为\n{{observable_behavior}}\n\n## 输入与输出\n{{inputs_outputs}}\n\n## 状态与异常场景\n{{states_and_exceptions}}\n\n## 非目标\n{{non_goals}}\n\n## 验收标准\n{{acceptance_criteria}}\n\n## 证据引用\n{{evidence_refs}}\n",
    },
    "detailed_design": {
        "path": "templates/default.md",
        "format": "markdown",
        "required_sections": ["模块职责", "接口与数据", "状态与错误处理", "Codebase Impact 证据", "Symbol Lookup 证据", "Call Graph 证据", "Include Impact 证据", "Macro / Type / Variable 证据", "代码影响上下文"],
        "content": "# 软件详细设计\n\n## 模块职责\n{{module_responsibilities}}\n\n## 接口与数据\n{{interfaces_and_data}}\n\n## 状态与错误处理\n{{state_and_error_handling}}\n\n## Codebase Impact 证据\n{{codebase_impact}}\n\n## Symbol Lookup 证据\n{{symbol_lookup}}\n\n## Call Graph 证据\n{{call_graph}}\n\n## Include Impact 证据\n{{include_impact}}\n\n## Macro / Type / Variable 证据\n{{macro_type_variable}}\n\n## 代码影响上下文\n{{code_impact_context}}\n",
    },
    "test_case_spec": {
        "path": "templates/default.json",
        "format": "json_table",
        "required_sections": ["columns", "rows", "normal", "boundary", "exception"],
        "content": '{\n  "columns": ["id", "requirement_ref", "scenario", "precondition", "steps", "expected", "evidence"],\n  "rows": [\n    {"id": "TC-001", "scenario": "normal 正常路径", "expected": "{{normal_expected}}"},\n    {"id": "TC-002", "scenario": "boundary 边界条件", "expected": "{{boundary_expected}}"},\n    {"id": "TC-003", "scenario": "exception 异常场景", "expected": "{{exception_expected}}"}\n  ]\n}\n',
    },
}


def ensure_default_document_skills(db_path: Path, *, workspace: Path, project_id: int) -> list[dict[str, Any]]:
    workspace = workspace.expanduser().resolve()
    skills_root = _skills_root(workspace)
    skills_root.mkdir(parents=True, exist_ok=True)
    _hide_retired_skills(db_path, project_id=project_id)
    ensured: list[dict[str, Any]] = []
    for spec in DEFAULT_DOCUMENT_SKILLS:
        markdown = _default_skill_markdown(spec)
        skill_dir = skills_root / spec.name
        skill_path = skill_dir / "SKILL.md"
        skill_dir.mkdir(parents=True, exist_ok=True)
        existing_skill_markdown = read_text(skill_path) if skill_path.exists() else ""
        should_upgrade_legacy_skill_file = bool(existing_skill_markdown) and _needs_default_skill_upgrade(existing_skill_markdown, spec)
        if not skill_path.exists():
            skill_path.write_text(markdown, encoding="utf-8")
        elif should_upgrade_legacy_skill_file:
            skill_path.write_text(markdown, encoding="utf-8")
        template_spec = DEFAULT_TEMPLATE_SPECS[spec.document_type]
        template_path = skill_dir / str(template_spec["path"])
        template_path.parent.mkdir(parents=True, exist_ok=True)
        if not template_path.exists():
            template_path.write_text(str(template_spec["content"]), encoding="utf-8")
        now = utc_now()
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM personal_skills
                WHERE project_id=? AND name=?
                """,
                (project_id, spec.name),
            ).fetchone()
            if row is None:
                skill_uid = f"skill_{uuid4().hex}"
                version_uid = f"skillver_{uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO personal_skills(
                        skill_uid, project_id, name, display_name, skill_kind, document_type,
                        description, status, active_version_uid, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'artifact_document', ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        skill_uid,
                        project_id,
                        spec.name,
                        spec.display_name,
                        spec.document_type,
                        spec.description,
                        version_uid,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO personal_skill_versions(
                        version_uid, skill_uid, project_id, version_index, skill_markdown,
                        metadata_json, status, created_by, created_at, activated_at
                    )
                    VALUES (?, ?, ?, 1, ?, ?, 'active', 'system_default', ?, ?)
                    """,
                    (
                        version_uid,
                        skill_uid,
                        project_id,
                        markdown,
                        json_dumps({"content_format": spec.content_format, "path": str(skill_path), "template_path": str(template_path)}),
                        now,
                        now,
                    ),
                )
            else:
                skill_uid = str(row["skill_uid"])
                version = conn.execute(
                    """
                    SELECT * FROM personal_skill_versions
                    WHERE skill_uid=? AND status='active'
                    ORDER BY version_index DESC LIMIT 1
                    """,
                    (skill_uid,),
                ).fetchone()
                active_version_uid = str(version["version_uid"]) if version is not None else f"skillver_{uuid4().hex}"
                if version is None:
                    conn.execute(
                        """
                        INSERT INTO personal_skill_versions(
                            version_uid, skill_uid, project_id, version_index, skill_markdown,
                            metadata_json, status, created_by, created_at, activated_at
                        )
                        VALUES (?, ?, ?, 1, ?, ?, 'active', 'system_repair', ?, ?)
                        """,
                        (
                            active_version_uid,
                            skill_uid,
                            project_id,
                            markdown,
                            json_dumps({"content_format": spec.content_format, "path": str(skill_path), "template_path": str(template_path)}),
                            now,
                            now,
                        ),
                    )
                elif should_upgrade_legacy_skill_file or _needs_default_skill_upgrade(str(version["skill_markdown"] or ""), spec):
                    active_version_uid = f"skillver_{uuid4().hex}"
                    next_index = int(version["version_index"]) + 1
                    skill_path.write_text(markdown, encoding="utf-8")
                    conn.execute(
                        """
                        INSERT INTO personal_skill_versions(
                            version_uid, skill_uid, project_id, version_index, skill_markdown,
                            metadata_json, status, created_by, created_at, activated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 'active', 'system_default_upgrade', ?, ?)
                        """,
                        (
                            active_version_uid,
                            skill_uid,
                            project_id,
                            next_index,
                            markdown,
                            json_dumps(
                                {
                                    "content_format": spec.content_format,
                                    "path": str(skill_path),
                                    "template_path": str(template_path),
                                    "upgrade_reason": "legacy_default_skill_missing_template_metadata",
                                }
                            ),
                            now,
                            now,
                        ),
                    )
                conn.execute(
                    """
                    UPDATE personal_skills
                    SET display_name=?, document_type=?, description=?, status='active',
                        active_version_uid=?, updated_at=?
                    WHERE skill_uid=?
                    """,
                    (spec.display_name, spec.document_type, spec.description, active_version_uid, now, skill_uid),
                )
        ensured.append(get_personal_skill(db_path, workspace=workspace, project_id=project_id, skill_name=spec.name))
    return ensured


def list_personal_skills(db_path: Path, *, workspace: Path, project_id: int) -> list[dict[str, Any]]:
    ensure_default_document_skills(db_path, workspace=workspace, project_id=project_id)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.*, v.version_index, v.version_uid, v.created_at AS version_created_at
            FROM personal_skills s
            LEFT JOIN personal_skill_versions v ON v.version_uid=s.active_version_uid
            WHERE s.project_id=? AND s.status='active' AND s.document_type IN ({})
            ORDER BY s.id
            """.format(",".join("?" for _ in DEFAULT_DOCUMENT_TYPES)),
            (project_id, *sorted(DEFAULT_DOCUMENT_TYPES)),
        ).fetchall()
    return [_skill_payload(db_path, workspace, row, include_markdown=False) for row in rows]


def get_personal_skill(db_path: Path, *, workspace: Path, project_id: int, skill_name: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT s.*, v.version_index, v.version_uid, v.created_at AS version_created_at, v.skill_markdown
            FROM personal_skills s
            LEFT JOIN personal_skill_versions v ON v.version_uid=s.active_version_uid
            WHERE s.project_id=? AND s.name=? AND s.status='active'
            """,
            (project_id, skill_name),
        ).fetchone()
    if row is None or str(row["document_type"]) not in DEFAULT_DOCUMENT_TYPES:
        raise ValueError("skill not found")
    return _skill_payload(db_path, workspace, row, include_markdown=True)


def list_personal_skill_versions(db_path: Path, *, project_id: int, skill_name: str) -> list[dict[str, Any]]:
    skill = _skill_row_by_name(db_path, project_id=project_id, skill_name=skill_name)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM personal_skill_versions
            WHERE project_id=? AND skill_uid=?
            ORDER BY version_index DESC
            """,
            (project_id, skill["skill_uid"]),
        ).fetchall()
    return [_version_payload(row) for row in rows]


def list_personal_skill_eval_runs(db_path: Path, *, project_id: int, skill_name: str) -> list[dict[str, Any]]:
    skill = _skill_row_by_name(db_path, project_id=project_id, skill_name=skill_name)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM personal_skill_eval_runs
            WHERE project_id=? AND skill_uid=?
            ORDER BY id DESC
            """,
            (project_id, skill["skill_uid"]),
        ).fetchall()
    return [_eval_payload(row) for row in rows]


def evaluate_personal_skill(db_path: Path, *, workspace: Path, project_id: int, skill_name: str) -> dict[str, Any]:
    skill = get_personal_skill(db_path, workspace=workspace, project_id=project_id, skill_name=skill_name)
    checks = _evaluate_markdown(skill)
    passed = all(item["passed"] for item in checks)
    score = round(sum(1 for item in checks if item["passed"]) / max(1, len(checks)), 3)
    eval_uid = f"skilleval_{uuid4().hex}"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personal_skill_eval_runs(
                eval_uid, skill_uid, version_uid, project_id, status, score, checks_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eval_uid,
                skill["skill_uid"],
                skill.get("active_version_uid") or "",
                project_id,
                "passed" if passed else "failed",
                score,
                json_dumps(checks),
                utc_now(),
            ),
        )
        row = conn.execute("SELECT * FROM personal_skill_eval_runs WHERE eval_uid=?", (eval_uid,)).fetchone()
    return _eval_payload(row)


def load_skill_for_document_type(db_path: Path, *, workspace: Path, project_id: int, document_type: str) -> dict[str, Any]:
    ensure_default_document_skills(db_path, workspace=workspace, project_id=project_id)
    if document_type not in DEFAULT_DOCUMENT_TYPES:
        raise ValueError(f"no skill registered for document_type: {document_type}")
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT name FROM personal_skills
            WHERE project_id=? AND document_type=? AND status='active'
            """,
            (project_id, document_type),
        ).fetchone()
    if row is None:
        raise ValueError(f"no skill registered for document_type: {document_type}")
    return get_personal_skill(db_path, workspace=workspace, project_id=project_id, skill_name=str(row["name"]))


def parse_skill_markdown(markdown: str) -> tuple[dict[str, Any], str]:
    text = markdown.strip()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        metadata = yaml.safe_load(parts[1].strip()) or {}
    except Exception:
        metadata = {}
    return (metadata if isinstance(metadata, dict) else {}), parts[2].strip()


def _hide_retired_skills(db_path: Path, *, project_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE personal_skills
            SET status='deleted', updated_at=?
            WHERE project_id=? AND document_type NOT IN ({})
            """.format(",".join("?" for _ in DEFAULT_DOCUMENT_TYPES)),
            (utc_now(), project_id, *sorted(DEFAULT_DOCUMENT_TYPES)),
        )


def _skills_root(workspace: Path) -> Path:
    return workspace / ".personal_agent" / "skills"


def _default_skill_markdown(spec: DefaultDocumentSkill) -> str:
    template_spec = DEFAULT_TEMPLATE_SPECS[spec.document_type]
    template_path = spec.template_path or str(template_spec["path"])
    template_format = spec.template_format or str(template_spec["format"])
    required_sections = spec.required_sections or tuple(str(item) for item in template_spec["required_sections"])
    required_sections_yaml = "".join(f"  - {item}\n" for item in required_sections)
    return f"""---
name: {spec.name}
description: {spec.description}
skill_kind: document
document_type: {spec.document_type}
content_format: {spec.content_format}
template:
  name: default
  path: {template_path}
  format: {template_format}
required_sections:
{required_sections_yaml}
allowed_tools:
  - source_read
  - knowledge_search
  - memory_read
  - code_evidence_read
---
# {spec.display_name}

## Purpose
{spec.description}

## Instructions
{spec.instructions}

## Output Contract
- Return only the document payload requested by the runtime JSON schema.
- Use the provided source, knowledge, memory and code evidence.
- Cite evidence identifiers when available.
- Keep the result as a personal draft only.
- Do not create release records, patch_apply actions, or real code changes.
"""


def _needs_default_skill_upgrade(markdown: str, spec: DefaultDocumentSkill) -> bool:
    metadata, _body = parse_skill_markdown(markdown)
    if not metadata:
        return True
    if str(metadata.get("name") or spec.name) != spec.name:
        return False
    document_type = str(metadata.get("document_type") or "").strip()
    if document_type != spec.document_type:
        return True
    template_meta = metadata.get("template") if isinstance(metadata.get("template"), dict) else {}
    required_sections = metadata.get("required_sections") if isinstance(metadata.get("required_sections"), list) else []
    return not template_meta or not required_sections


def _skill_path(workspace: Path, name: str) -> Path:
    return _skills_root(workspace) / name / "SKILL.md"


def _skill_payload(db_path: Path, workspace: Path, row: Any, *, include_markdown: bool) -> dict[str, Any]:
    skill_path = _skill_path(workspace, str(row["name"]))
    db_markdown = str(row["skill_markdown"] or "") if "skill_markdown" in row.keys() else ""
    current_markdown = read_text(skill_path) if skill_path.exists() else db_markdown
    metadata, _body = parse_skill_markdown(current_markdown)
    template_meta = metadata.get("template") if isinstance(metadata.get("template"), dict) else {}
    required_sections = metadata.get("required_sections") if isinstance(metadata.get("required_sections"), list) else []
    template_path = str((skill_path.parent / str(template_meta.get("path") or "templates/default.md")).resolve())
    payload = {
        "id": row["id"],
        "skill_uid": row["skill_uid"],
        "project_id": row["project_id"],
        "name": row["name"],
        "display_name": row["display_name"],
        "skill_kind": "document",
        "document_type": row["document_type"],
        "description": row["description"],
        "status": row["status"],
        "active_version_uid": row["active_version_uid"],
        "active_version_index": row["version_index"] if "version_index" in row.keys() else None,
        "path": str(skill_path),
        "exists": skill_path.exists(),
        "frontmatter": metadata,
        "template": {
            "name": str(template_meta.get("name") or "default"),
            "path": template_path,
            "relative_path": str(template_meta.get("path") or "templates/default.md"),
            "format": str(template_meta.get("format") or "markdown"),
            "required_sections": [str(item) for item in required_sections],
            "hash": _hash_text(read_text(Path(template_path)) if Path(template_path).exists() else ""),
            "loaded": Path(template_path).exists(),
        },
        "required_sections": [str(item) for item in required_sections],
        "eval_runs": list_personal_skill_eval_runs(db_path, project_id=int(row["project_id"]), skill_name=str(row["name"])),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_markdown:
        payload["skill_markdown"] = current_markdown
    return payload


def _version_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "version_uid": row["version_uid"],
        "skill_uid": row["skill_uid"],
        "project_id": row["project_id"],
        "version_index": row["version_index"],
        "skill_markdown": row["skill_markdown"],
        "metadata": _loads_json(row["metadata_json"], {}),
        "status": row["status"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "activated_at": row["activated_at"],
    }


def _eval_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "eval_uid": row["eval_uid"],
        "skill_uid": row["skill_uid"],
        "version_uid": row["version_uid"],
        "project_id": row["project_id"],
        "status": row["status"],
        "score": row["score"],
        "checks": _loads_json(row["checks_json"], []),
        "created_at": row["created_at"],
    }


def _skill_row_by_name(db_path: Path, *, project_id: int, skill_name: str) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM personal_skills
            WHERE project_id=? AND name=? AND status='active'
            """,
            (project_id, skill_name),
        ).fetchone()
    if row is None or str(row["document_type"]) not in DEFAULT_DOCUMENT_TYPES:
        raise ValueError("skill not found")
    return dict(row)


def _evaluate_markdown(skill: dict[str, Any]) -> list[dict[str, Any]]:
    markdown = str(skill.get("skill_markdown") or "")
    frontmatter = skill.get("frontmatter") if isinstance(skill.get("frontmatter"), dict) else {}
    allowed_tools = frontmatter.get("allowed_tools") if isinstance(frontmatter, dict) else []
    checks = [
        {"name": "has_frontmatter_name", "passed": bool(frontmatter.get("name") == skill["name"])},
        {"name": "document_type_matches", "passed": bool(frontmatter.get("document_type") == skill["document_type"])},
        {"name": "is_document_skill", "passed": bool(frontmatter.get("skill_kind") == "document")},
        {"name": "has_output_contract", "passed": "Output Contract" in markdown or "输出" in markdown},
        {"name": "declares_tools", "passed": isinstance(allowed_tools, list) and bool(allowed_tools)},
        {"name": "personal_draft_boundary", "passed": "personal draft" in markdown.lower()},
    ]
    return checks


def _loads_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
