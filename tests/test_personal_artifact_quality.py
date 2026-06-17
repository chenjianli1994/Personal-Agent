from __future__ import annotations

from personal_agent.artifact_quality import validate_generated_artifact


def test_functional_spec_rejects_implementation_details() -> None:
    result = validate_generated_artifact(
        document_type="functional_spec",
        content_format="markdown",
        content="# 功能规范\n\n## 行为\n```c\nint Foo(void) { return 1; }\n```\n\n## 证据引用\n- source: current_prompt",
        context=_context(),
        skill=_skill("functional_spec"),
        llm_result=_llm(),
    )
    assert result["passed"] is False
    assert any(item["name"] == "no_implementation_details" and not item["passed"] for item in result["checks"])


def test_detailed_design_requires_code_evidence_sections_and_context() -> None:
    bad = validate_generated_artifact(
        document_type="detailed_design",
        content_format="markdown",
        content="# 详细设计\n\n## 证据引用\n- source: current_prompt",
        context=_context(impact={}),
        skill=_skill("detailed_design"),
        llm_result=_llm(),
    )
    assert bad["passed"] is False

    good = validate_generated_artifact(
        document_type="detailed_design",
        content_format="markdown",
        content="# 详细设计\n\n## Codebase Impact 证据\n- ok\n\n## Symbol Lookup 证据\n- VehicleSpeed_Read\n\n## 证据引用\n- source: current_prompt",
        context=_context(impact={"passed": True, "candidate_symbols": [{"name": "VehicleSpeed_Read"}]}),
        skill=_skill("detailed_design"),
        llm_result=_llm(),
    )
    assert good["passed"] is True


def test_test_case_spec_requires_json_table_and_scenario_coverage() -> None:
    rejected = validate_generated_artifact(
        document_type="test_case_spec",
        content_format="json_table",
        content='{"columns":["id"],"rows":[{"id":"TC-001","scenario":"normal"}]}',
        context=_context(),
        skill=_skill("test_case_spec"),
        llm_result=_llm(),
    )
    assert rejected["passed"] is False

    accepted = validate_generated_artifact(
        document_type="test_case_spec",
        content_format="json_table",
        content='{"columns":["id","scenario"],"rows":[{"id":"TC-001","scenario":"normal 正常"},{"id":"TC-002","scenario":"boundary 边界"},{"id":"TC-003","scenario":"exception 异常"}]}',
        context=_context(),
        skill=_skill("test_case_spec"),
        llm_result=_llm(),
    )
    assert accepted["passed"] is True


def test_forbidden_personal_language_fails_quality() -> None:
    result = validate_generated_artifact(
        document_type="requirement_analysis_report",
        content_format="markdown",
        content="# 需求分析\n\n包含 " + "AS" + "PICE" + " 旧词。\n\n## 证据引用\n- source: current_prompt",
        context=_context(),
        skill=_skill("requirement_analysis_report"),
        llm_result=_llm(),
    )
    assert result["passed"] is False
    assert any(item["name"] == "personal_language_clean" and not item["passed"] for item in result["checks"])


def _context(impact: dict | None = None) -> dict:
    return {
        "evidence_refs": {"active_source_uids": ["current_prompt"]},
        "impact": {"passed": True} if impact is None else impact,
    }


def _skill(document_type: str) -> dict:
    return {"document_type": document_type}


def _llm() -> dict:
    return {"llm_call_id": 1}
