from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_agent.content_guard import FORBIDDEN_PERSONAL_TERMS
from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.database import connect
from personal_agent.app import create_personal_app


LLMResult = getattr(llm_gateway_module, "LLMResult")
LLMBridge = getattr(llm_gateway_module, "PersonalLLMGateway")
LLMError = getattr(llm_gateway_module, "PersonalLLM" + "Error")


def _valid_requirement_analysis_content(requirement_line: str = "初版需求理解。") -> str:
    return (
        "# 需求分析报告\n\n"
        "## 输入摘要\n"
        "- source: current_prompt\n\n"
        "## 原文事实表\n"
        "- 源文包含水泵和电子风扇两类控制逻辑。\n\n"
        "## 术语与变量定义\n"
        "- A：OBC进入慢充状态时的初始水温。\n"
        "- B：OBC进入慢充状态后的实时水温。\n\n"
        "## 需求理解\n"
        f"- {requirement_line}\n\n"
        "## 条件与状态机\n"
        "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| OBC进入慢充后 | OBC_WorkSts 与 BMS_BattChargerSts 组合满足慢充 | 水泵、电子风扇 | 按温度条件调节 | 进入慢充完成后 | current_prompt |\n"
        "| OBC慢充完成后 | 接收充电完成信号 | 水泵、电子风扇 | 保持 10 分钟 | 整车休眠 | current_prompt |\n\n"
        "## 歧义与待确认\n"
        "- 当前输入无额外歧义。\n\n"
        "## 关键假设\n"
        "- 未确认的信息保持待澄清。\n\n"
        "## 风险与边界\n"
        "- 明确外部行为边界和异常条件。\n\n"
        "## 验收建议\n"
        "- 按输入、输出、边界和异常逐项验收。\n\n"
        "## 证据引用\n"
        "- source: current_prompt\n"
    )


def _assert_quality_failed_draft(response: Any, expected: str) -> dict[str, Any]:
    assert response.status_code == 200, response.json()
    draft = response.json()
    assert draft["status"] == "quality_failed"
    quality = draft["metadata"]["generation"]["quality"]
    assert quality["passed"] is False
    assert any(expected in str(item) for item in quality["blocking_failures"])
    return draft


def test_personal_document_generation_uses_skill_template_and_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    calls: list[dict[str, str]] = []

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        calls.append({"purpose": purpose, "system_prompt": system_prompt, "user_prompt": user_prompt})
        return LLMResult(
            call_id=123,
            provider="fake-test",
            model="skill-doc-fixture",
            status="ok",
            parsed={
                "title": "LLM 功能规范",
                "content_format": "markdown",
                "content": "# LLM 功能规范\n\n## 功能目标\n- 基于 source 输入定义用户可验证行为。\n\n## 用户可观察行为\n- 外部输入、输出、异常提示和验收路径均可观察。\n\n## 输入与输出\n- 输入来自当前提示；输出为功能行为说明。\n\n## 状态与异常场景\n- 覆盖正常、边界和异常状态。\n\n## 非目标\n- 不包含代码级实现或内部算法。\n\n## 验收标准\n- 可按证据来源逐项验收。\n\n## 证据引用\n- source: current_prompt\n",
            },
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成用户可验证的功能规范", "document_type": "functional_spec"},
    )
    assert response.status_code == 200, response.json()
    draft = response.json()
    assert calls
    assert calls[0]["purpose"] == "personal_artifact_generate"
    assert "SKILL.md" in calls[0]["system_prompt"]
    assert '"document_type": "functional_spec"' in calls[0]["user_prompt"]
    assert '"template"' in calls[0]["user_prompt"]
    assert "## 功能目标" in calls[0]["user_prompt"]
    assert draft["document_type"] == "functional_spec"
    assert draft["title"] == "LLM 功能规范"
    generation = draft["metadata"]["generation"]
    assert generation["skill"]["name"] == "functional-spec"
    assert generation["template"]["name"] == "default"
    assert generation["template"]["format"] == "markdown"
    assert generation["template"]["hash"]
    assert generation["llm"]["call_id"] == 123

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_forbidden_generated_content_fails_before_draft_is_saved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        return LLMResult(
            call_id=124,
            provider="fake-test",
            model="skill-doc-fixture",
            status="ok",
            parsed={
                "title": "坏内容",
                "content_format": "markdown",
                "content": f"# 坏内容\n\n这里包含 {FORBIDDEN_PERSONAL_TERMS[0]} 旧流程词。\n\n## 证据引用\n- source: current_prompt\n",
            },
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report"},
    )
    assert response.status_code == 400
    assert "forbidden" in response.json()["detail"]
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 0


def test_missing_required_sections_returns_quality_failed_draft(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        return LLMResult(
            call_id=125,
            provider="fake-test",
            model="skill-doc-fixture",
            status="ok",
            parsed={
                "title": "缺章节功能规范",
                "content_format": "markdown",
                "content": "# 功能规范\n\n## 功能目标\n- 有目标。\n\n## 证据引用\n- source: current_prompt\n",
            },
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成功能规范", "document_type": "functional_spec"},
    )
    _assert_quality_failed_draft(response, "required_sections")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1
        candidate = conn.execute("SELECT * FROM personal_skill_update_candidates ORDER BY id DESC LIMIT 1").fetchone()
    assert candidate is not None
    assert candidate["target_skill"] == "functional-spec"
    assert candidate["status"] == "candidate"
    assert candidate["source"] == "quality_check_failure"


def test_unresolved_template_placeholder_returns_quality_failed_draft(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        return LLMResult(
            call_id=126,
            provider="fake-test",
            model="skill-doc-fixture",
            status="ok",
            parsed={
                "title": "占位符功能规范",
                "content_format": "markdown",
                "content": "# 功能规范\n\n## 功能目标\n- {{functional_goal}}\n\n## 用户可观察行为\n- 可观察。\n\n## 输入与输出\n- 输入输出。\n\n## 状态与异常场景\n- 状态异常。\n\n## 非目标\n- 非目标。\n\n## 验收标准\n- 验收。\n\n## 证据引用\n- source: current_prompt\n",
            },
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成功能规范", "document_type": "functional_spec"},
    )
    _assert_quality_failed_draft(response, "{{placeholder}}")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_preserves_source_ambiguity_and_rejects_unsourced_code_refs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_artifact_generate":
            content = (
                "# 需求分析报告\n\n"
                "## 输入摘要\n"
                "- 来源证据：current_prompt\n\n"
                "## 原文事实表\n"
                "- 源文包含水泵和电子风扇两类控制逻辑。\n\n"
                "## 术语与变量定义\n"
                "- A：OBC进入慢充状态时的初始水温。\n"
                "- B：OBC进入慢充状态后的实时水温。\n"
                "- IB-A|：源文原始差值表达，需保留并待确认。\n\n"
                "## 需求理解\n"
                "- 保留原文状态名 Praking Charging、ilde 和 IB-A| 的歧义。\n\n"
                "## 条件与状态机\n"
                "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| OBC进入慢充后 | OBC_WorkSts 与 BMS_BattChargerSts 组合满足慢充 | 水泵、电子风扇 | 按环境温度和 A/B 差值控制 | 进入慢充完成后 | current_prompt |\n"
                "| OBC慢充完成后 | 充电完成状态被识别 | 水泵、电子风扇 | 保持 10 分钟 | 整车休眠 | current_prompt |\n\n"
                "## 歧义与待确认\n"
                "- Praking Charging、ilde、IB-A| 需要待确认。\n\n"
                "## 关键假设\n"
                "- 当前只做需求分析，不补代码实现。\n\n"
                "## 风险与边界\n"
                "- 温度边界需要后续明确。\n\n"
                "## 验收建议\n"
                "- 需覆盖状态切换和边界值。\n\n"
                "## 证据引用\n"
                "- source: current_prompt\n"
            )
        else:
            content = (
                "# 需求分析报告\n\n"
                "## 输入摘要\n"
                "- 来源证据：current_prompt\n\n"
                "## 原文事实表\n"
                "- 源文包含水泵和电子风扇两类控制逻辑。\n\n"
                "## 术语与变量定义\n"
                "- A：OBC进入慢充状态时的初始水温。\n"
                "- B：OBC进入慢充状态后的实时水温。\n\n"
                "## 需求理解\n"
                "- 结合 `src/thermal_manager.h` 和 `src/thermal_manager.c` 的实现理解需求。\n\n"
                "## 条件与状态机\n"
                "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| OBC进入慢充后 | OBC_WorkSts 与 BMS_BattChargerSts 组合满足慢充 | 水泵、电子风扇 | 按温度条件调节 | 进入完成后处理 | current_prompt |\n"
                "| OBC慢充完成后 | 充电完成状态被识别 | 水泵、电子风扇 | 保持 10 分钟 | 整车休眠 | current_prompt |\n\n"
                "## 歧义与待确认\n"
                "- Praking Charging、ilde、IB-A| 需要待确认。\n\n"
                "## 关键假设\n"
                "- 当前只做需求分析，不补代码实现。\n\n"
                "## 风险与边界\n"
                "- 温度边界需要后续明确。\n\n"
                "## 验收建议\n"
                "- 需覆盖状态切换和边界值。\n\n"
                "## 证据引用\n"
                "- source: current_prompt\n"
            )
        return LLMResult(
            call_id=201 if purpose == "personal_artifact_generate" else 202,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "OBC慢充热管理策略需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    source = client.post("/api/personal/sources/text", json={"title": "OBC需求", "content": "见当前输入资料"})
    assert source.status_code == 200

    generated = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )
    assert generated.status_code == 200
    draft = generated.json()
    assert "Praking Charging" in draft["content"]
    assert "ilde" in draft["content"]
    assert "IB-A|" in draft["content"]
    assert "thermal_manager.h" not in draft["content"]
    assert "thermal_manager.c" not in draft["content"]

    bad = client.post(
        f"/api/personal/drafts/{draft['draft_uid']}/revise",
        json={"feedback": "补充代码证据引用"},
    )
    failed = _assert_quality_failed_draft(bad, "证据")
    assert failed["current_revision"] == 2


def test_requirement_analysis_report_quality_blocks_unsourced_code_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n"
            "- 来源证据：current_prompt\n\n"
            "## 原文事实表\n"
            "- 源文包含水泵和电子风扇两类控制逻辑。\n\n"
            "## 需求理解\n"
            "- 引用 `src/thermal_manager.h` 和 `src/thermal_manager.c` 作为实现证据。\n\n"
            "## 条件与状态机\n"
            "- 慢充状态由 OBC_WorkSts 和 BMS_BattChargerSts 组合判定。\n\n"
            "## 歧义与待确认\n"
            "- Praking Charging、ilde、IB-A| 需要待确认。\n\n"
            "## 关键假设\n"
            "- 当前只做需求分析，不补代码实现。\n\n"
            "## 风险与边界\n"
            "- 温度边界需要后续明确。\n\n"
            "## 验收建议\n"
            "- 需覆盖状态切换和边界值。\n\n"
            "## 证据引用\n"
            "- source: current_prompt\n"
        )
        return LLMResult(
            call_id=301 if purpose == "personal_artifact_generate" else 302,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "OBC慢充热管理策略需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    source = client.post("/api/personal/sources/text", json={"title": "OBC需求", "content": "见当前输入资料"})
    assert source.status_code == 200

    generated = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )
    _assert_quality_failed_draft(generated, "证据")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_uses_default_sections_when_existing_skill_lacks_frontmatter_template(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    calls: list[dict[str, str]] = []

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        calls.append({"purpose": purpose, "system_prompt": system_prompt, "user_prompt": user_prompt})
        return LLMResult(
            call_id=401,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={
                "title": "旧 Skill 需求分析报告",
                "content_format": "markdown",
                "content": (
                    "# 需求分析报告\n\n"
                    "## 输入摘要\n- source: current_prompt\n\n"
                    "## 需求理解\n- 旧版松散结构。\n\n"
                    "## 证据引用\n- source: current_prompt\n"
                ),
            },
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_dir = workspace / ".personal_agent" / "skills" / "requirement-analysis-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: requirement-analysis-report
description: 旧版需求分析报告 Skill
skill_kind: document
document_type: requirement_analysis_report
content_format: markdown
allowed_tools:
  - source_read
---
# 需求分析报告

## Instructions
生成 Markdown 需求分析报告。必须包含：输入摘要、需求理解、关键假设、风险与边界、待澄清问题、验收建议、证据引用。
""",
        encoding="utf-8",
    )
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report"},
    )

    _assert_quality_failed_draft(response, "required_sections")
    assert calls
    assert "原文事实表" in calls[0]["user_prompt"]
    assert "条件与状态机" in calls[0]["user_prompt"]
    assert "project_adapter_rules" in calls[0]["user_prompt"]
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_fact_table_rejects_inference_terms(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n"
            "- 来源证据：current_prompt\n\n"
            "## 原文事实表\n"
            "- 源文包含水泵和电子风扇两类控制逻辑，建议后续补充降级策略。\n\n"
            "## 需求理解\n"
            "- 分析控制策略边界。\n\n"
            "## 条件与状态机\n"
            "- 慢充状态由 OBC_WorkSts 和 BMS_BattChargerSts 组合判定。\n\n"
            "## 歧义与待确认\n"
            "- 当前输入无额外歧义。\n\n"
            "## 关键假设\n"
            "- 未确认项保持待澄清。\n\n"
            "## 风险与边界\n"
            "- 温度边界需要确认。\n\n"
            "## 验收建议\n"
            "- 覆盖状态切换和边界值。\n\n"
            "## 证据引用\n"
            "- source: current_prompt\n"
        )
        return LLMResult(
            call_id=402,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report"},
    )

    _assert_quality_failed_draft(response, "原文事实")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_preserves_variable_definitions_and_lifecycle_phases(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n"
            "- 来源证据：current_prompt\n\n"
            "## 原文事实表\n"
            "- 源文定义 A 和 B 的物理含义，并区分 OBC进入慢充后 与 OBC慢充完成后。\n\n"
            "## 术语与变量定义\n"
            "- A：OBC进入慢充状态时的初始水温（CDU_RealityTemp）。\n"
            "- B：OBC进入慢充状态后的实时水温（CDU_RealityTemp）。\n"
            "- IB-A|：源文原始差值表达，疑似为 B 与 A 的差值但需保留原文。\n\n"
            "## 需求理解\n"
            "- 水泵和风扇策略需要按进入慢充后的运行控制与慢充完成后的保持逻辑分别理解。\n\n"
            "## 条件与状态机\n"
            "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| OBC进入慢充后 | OBC_WorkSts=Charging 且 BMS_BattChargerSts=Praking Charging | 水泵、电子风扇 | 按 TMS 环境温度、A/B 差值或 CDU 水温调节 | 检测到慢充完成 | current_prompt |\n"
            "| OBC慢充完成后 | OBC_WorkSts=ilde 且 BMS_BattChargerSts=Charge Finished | 高压保持、水泵、电子风扇 | ON2 保持 10 分钟，执行 50% 转速保持 | 10 分钟后整车休眠 | current_prompt |\n\n"
            "## 歧义与待确认\n"
            "- Praking Charging、ilde、IB-A| 需保留并待确认。\n\n"
            "## 关键假设\n"
            "- 当前只做需求分析，不补代码实现。\n\n"
            "## 风险与边界\n"
            "- 阶段边界和信号稳定性需要确认。\n\n"
            "## 验收建议\n"
            "- 分别覆盖进入慢充后的运行控制和慢充完成后的 10 分钟保持。\n\n"
            "## 证据引用\n"
            "- source: current_prompt\n"
        )
        return LLMResult(
            call_id=501,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "OBC慢充热管理策略需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    source_text = (
        "水泵策略：A:OBC进入慢充状态时的初始水温（CDU_RealityTemp）；"
        "B:OBC进入慢充状态后的实时水温（CDU_RealityTemp）；"
        "OBC进入慢充后，当 IB-A| 的差值变化时调节水泵。"
        "OBC慢充完成后，TMS接收充电完成信号且CDU水温>=20℃时，执行10分钟保持。"
        "BMS_BattChargerSts会发Praking Charging，当结束充电，OBC_WorkSts发ilde。"
    )
    source = client.post("/api/personal/sources/text", json={"title": "OBC需求", "content": source_text})
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    assert response.status_code == 200, response.json()
    content = response.json()["content"]
    assert "术语与变量定义" in content
    assert "OBC进入慢充状态时的初始水温" in content
    assert "OBC进入慢充状态后的实时水温" in content
    assert "OBC进入慢充后" in content
    assert "OBC慢充完成后" in content
    checks = response.json()["metadata"]["generation"]["quality"]["checks"]
    assert {item["name"] for item in checks} >= {
        "term_definitions_present",
        "source_variable_definitions_preserved",
        "defined_variable_usage_preserves_semantics",
        "lifecycle_phases_separated",
        "state_precondition_covers_control_branches",
    }


def test_requirement_analysis_report_restores_original_ambiguous_symbol_when_llm_normalizes_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_source_semantic_model":
            return LLMResult(
                call_id=711,
                provider="fake-test",
                model="semantic-fixture",
                status="ok",
                parsed={
                    "defined_terms": [
                        {
                            "symbol": "A",
                            "source_definition": "A:进入运行状态时的初始温度",
                            "physical_meaning": "进入运行状态时的初始温度",
                            "effective_timing": "进入运行状态时",
                            "unit": "℃",
                            "applies_to": "执行器",
                            "calculation_usage": "与B组成温度差值",
                            "evidence_quote": "A:进入运行状态时的初始温度",
                        },
                        {
                            "symbol": "B",
                            "source_definition": "B:进入运行状态后的实时温度",
                            "physical_meaning": "进入运行状态后的实时温度",
                            "effective_timing": "进入运行状态后",
                            "unit": "℃",
                            "applies_to": "执行器",
                            "calculation_usage": "与A组成温度差值",
                            "evidence_quote": "B:进入运行状态后的实时温度",
                        },
                    ],
                    "state_phases": [
                        {
                            "phase_name": "进入运行状态后",
                            "state_precondition": "进入运行状态后",
                            "entry_condition": "进入运行状态",
                            "in_phase_condition": "IB-A|差值升高",
                            "exit_or_completion": "完成后",
                            "post_completion_handling": "保持10分钟",
                            "evidence_quote": "进入运行状态后，当 IB-A| 的差值升高时调节执行器；完成后保持10分钟",
                        }
                    ],
                    "control_branches": [
                        {
                            "state_precondition": "进入运行状态后",
                            "controlled_object": "执行器",
                            "secondary_condition": "IB-A|差值升高",
                            "output_strategy": "线性调节执行器",
                            "exit_or_completion": "完成后",
                            "evidence_quote": "进入运行状态后，当 IB-A| 的差值升高时调节执行器",
                        }
                    ],
                    "open_questions": [],
                },
                raw_text="{}",
            )
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n- 来源证据：current_prompt\n\n"
            "## 原文事实表\n- 源文定义 A、B，并描述进入运行状态后的控制策略。\n\n"
            "## 术语与变量定义\n"
            "| 符号 | 源文定义 | 物理含义 | 单位 | 适用对象 | 生效/采样时刻 | 计算关系 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| A | A:进入运行状态时的初始温度 | 进入运行状态时的初始温度 | ℃ | 执行器 | 进入运行状态时 | 与B组成温度差值 | current_prompt |\n"
            "| B | B:进入运行状态后的实时温度 | 进入运行状态后的实时温度 | ℃ | 执行器 | 进入运行状态后 | 与A组成温度差值 | current_prompt |\n"
            "| |B-A| | LLM规范化后的差值表达 | 实时温度与初始温度差值 | ℃ | 执行器 | 进入运行状态后 | 线性调节执行器 | current_prompt |\n\n"
            "## 需求理解\n- 先判断进入运行状态后，再根据温度差值调节执行器。\n\n"
            "## 条件与状态机\n"
            "| 阶段 | 进入条件 | 运行中条件 | 适用对象 | 控制策略 | 退出/完成条件 | 完成后处理 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 进入运行状态后 | 进入运行状态后 | |B-A|差值升高 | 执行器 | 线性调节执行器 | 完成后 | 保持10分钟 | current_prompt |\n\n"
            "## 歧义与待确认\n- 当前无额外歧义。\n\n"
            "## 关键假设\n- 未提供的异常降级不作为事实写入。\n\n"
            "## 风险与边界\n- 差值符号需要后续确认。\n\n"
            "## 验收建议\n- 覆盖进入运行状态后、差值边界和完成后保持。\n\n"
            "## 证据引用\n- source: current_prompt\n"
        )
        return LLMResult(
            call_id=712,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "通用执行器需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    source = client.post(
        "/api/personal/sources/text",
        json={"title": "通用需求", "content": "A:进入运行状态时的初始温度；B:进入运行状态后的实时温度；进入运行状态后，当 IB-A| 的差值升高时调节执行器；完成后保持10分钟。"},
    )
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    assert response.status_code == 200, response.json()
    content = response.json()["content"]
    assert "IB-A|" in content
    assert "源文原始符号表达" in content
    checks = {item["name"]: item["passed"] for item in response.json()["metadata"]["generation"]["quality"]["checks"]}
    assert checks["source_ambiguity_preserved"] is True


def test_requirement_analysis_report_injects_generic_semantic_model_without_business_keywords(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    calls: list[dict[str, Any]] = []

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        calls.append({"purpose": purpose, "user_prompt": user_prompt})
        if purpose == "personal_source_semantic_model":
            payload = json.loads(user_prompt)
            assert "business-specific dictionaries" in " ".join(payload["constraints"])
            return LLMResult(
                call_id=701,
                provider="fake-test",
                model="semantic-fixture",
                status="ok",
                parsed={
                    "defined_terms": [
                        {
                            "symbol": "T1",
                            "source_definition": "T1:进入运行状态时的初始压力",
                            "physical_meaning": "进入运行状态时的初始压力",
                            "effective_timing": "进入运行状态时",
                            "unit": "kPa",
                            "applies_to": "压力采样",
                            "calculation_usage": "与T2组成压力差值",
                            "evidence_quote": "T1:进入运行状态时的初始压力",
                        },
                        {
                            "symbol": "T2",
                            "source_definition": "T2:进入运行状态后的实时压力",
                            "physical_meaning": "进入运行状态后的实时压力",
                            "effective_timing": "进入运行状态后",
                            "unit": "kPa",
                            "applies_to": "压力采样",
                            "calculation_usage": "与T1组成压力差值",
                            "evidence_quote": "T2:进入运行状态后的实时压力",
                        },
                    ],
                    "state_phases": [
                        {
                            "phase_name": "进入运行状态后",
                            "state_precondition": "进入运行状态后",
                            "entry_condition": "进入运行状态",
                            "in_phase_condition": "压力差超过阈值",
                            "exit_or_completion": "完成后",
                            "post_completion_handling": "保持10分钟",
                            "evidence_quote": "进入运行状态后，若压力差超过阈值则调节执行器；完成后保持10分钟",
                        }
                    ],
                    "control_branches": [
                        {
                            "state_precondition": "进入运行状态后",
                            "controlled_object": "执行器",
                            "secondary_condition": "压力差超过阈值",
                            "output_strategy": "调节执行器",
                            "exit_or_completion": "完成后",
                            "evidence_quote": "进入运行状态后，若压力差超过阈值则调节执行器",
                        }
                    ],
                    "open_questions": [],
                },
                raw_text="{}",
            )
        payload = json.loads(user_prompt)
        semantic_model = payload.get("source_semantic_model") or {}
        assert payload["document_type"] == "requirement_analysis_report"
        assert semantic_model["defined_terms"][0]["symbol"] == "T1"
        assert semantic_model["control_branches"][0]["state_precondition"] == "进入运行状态后"
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n- 来源证据：current_prompt\n\n"
            "## 原文事实表\n- 源文定义 T1、T2，并描述进入运行状态后与完成后两个阶段。\n\n"
            "## 术语与变量定义\n"
            "| 符号 | 源文定义 | 物理含义 | 单位 | 适用对象 | 生效/采样时刻 | 计算关系 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| T1 | T1:进入运行状态时的初始压力 | 进入运行状态时的初始压力 | kPa | 压力采样 | 进入运行状态时 | 与T2组成压力差值 | current_prompt |\n"
            "| T2 | T2:进入运行状态后的实时压力 | 进入运行状态后的实时压力 | kPa | 压力采样 | 进入运行状态后 | 与T1组成压力差值 | current_prompt |\n\n"
            "## 需求理解\n- 先判断进入运行状态后，再根据压力差超过阈值这一二级条件调节执行器。\n\n"
            "## 条件与状态机\n"
            "| 阶段 | 进入条件 | 运行中条件 | 适用对象 | 控制策略 | 退出/完成条件 | 完成后处理 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 进入运行状态后 | 进入运行状态后 | 压力差超过阈值 | 执行器 | 调节执行器 | 完成后 | 保持10分钟 | current_prompt |\n"
            "| 完成后 | 完成后 | 保持计时 | 执行器 | 保持10分钟 | 计时结束 | 退出保持 | current_prompt |\n\n"
            "## 歧义与待确认\n- 当前无额外歧义。\n\n"
            "## 关键假设\n- 未提供的异常降级不作为事实写入。\n\n"
            "## 风险与边界\n- 压力差阈值需要后续明确边界归属。\n\n"
            "## 验收建议\n- 覆盖进入运行状态后、压力差阈值和完成后保持10分钟。\n\n"
            "## 证据引用\n- source: current_prompt\n"
        )
        return LLMResult(
            call_id=702,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "通用压力控制需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    source = client.post(
        "/api/personal/sources/text",
        json={"title": "通用压力需求", "content": "T1:进入运行状态时的初始压力；T2:进入运行状态后的实时压力；进入运行状态后，若压力差超过阈值则调节执行器；完成后保持10分钟。"},
    )
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    assert response.status_code == 200, response.json()
    assert [item["purpose"] for item in calls] == ["personal_source_semantic_model", "personal_artifact_generate"]
    generation = response.json()["metadata"]["generation"]
    quality_checks = {item["name"]: item["passed"] for item in generation["quality"]["checks"]}
    assert quality_checks["defined_variable_usage_preserves_semantics"] is True
    assert quality_checks["state_precondition_covers_control_branches"] is True


def test_requirement_analysis_report_rejects_missing_variable_definition_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n- 来源证据：current_prompt\n\n"
            "## 原文事实表\n- 源文定义 A 和 B。\n\n"
            "## 需求理解\n- 只保留 A/B 名称但没有解释物理含义。\n\n"
            "## 条件与状态机\n"
            "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| OBC进入慢充后 | 进入慢充 | 水泵 | 调节 | OBC慢充完成后 | current_prompt |\n"
            "| OBC慢充完成后 | 完成后 | 水泵 | 保持 | 休眠 | current_prompt |\n\n"
            "## 歧义与待确认\n- 当前无。\n\n"
            "## 关键假设\n- 当前无。\n\n"
            "## 风险与边界\n- 当前无。\n\n"
            "## 验收建议\n- 覆盖阶段。\n\n"
            "## 证据引用\n- source: current_prompt\n"
        )
        return LLMResult(
            call_id=502,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    source = client.post(
        "/api/personal/sources/text",
        json={"title": "OBC需求", "content": "A:OBC进入慢充状态时的初始水温；B:OBC进入慢充状态后的实时水温；OBC进入慢充后控制，OBC慢充完成后保持。"},
    )
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    _assert_quality_failed_draft(response, "术语与变量定义")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_rejects_short_variables_without_semantics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n- 来源证据：current_prompt\n\n"
            "## 原文事实表\n- 源文定义 A 和 B，并描述进入状态与完成后动作。\n\n"
            "## 术语与变量定义\n"
            "- A：源文定义的变更量。\n"
            "- B：源文定义的变更量。\n\n"
            "## 需求理解\n- 只保留变量名，没有保留物理含义。\n\n"
            "## 条件与状态机\n"
            "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 进入运行后 | 进入运行 | 对象 | 使用 A/B 调节 | 完成后 | current_prompt |\n"
            "| 完成后 | 完成触发 | 对象 | 保持 | 退出 | current_prompt |\n\n"
            "## 歧义与待确认\n- 当前无。\n\n"
            "## 关键假设\n- 当前无。\n\n"
            "## 风险与边界\n- 当前无。\n\n"
            "## 验收建议\n- 覆盖阶段。\n\n"
            "## 证据引用\n- source: current_prompt\n"
        )
        return LLMResult(
            call_id=504,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    source = client.post(
        "/api/personal/sources/text",
        json={"title": "通用需求", "content": "A:进入运行状态时的初始温度；B:进入运行状态后的实时温度；进入运行后按差值调节；完成后保持10分钟。"},
    )
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    _assert_quality_failed_draft(response, "变量")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_rejects_computed_expression_that_drops_variable_semantics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_source_semantic_model":
            return LLMResult(
                call_id=610,
                provider="fake-test",
                model="semantic-fixture",
                status="ok",
                parsed={
                    "defined_terms": [
                        {
                            "symbol": "A",
                            "source_definition": "A:OBC进入慢充状态时的初始水温（CDU_RealityTemp）",
                            "physical_meaning": "OBC进入慢充状态时的初始水温",
                            "effective_timing": "OBC进入慢充状态时",
                            "unit": "℃",
                            "applies_to": "CDU_RealityTemp",
                            "calculation_usage": "与B组成水温差值",
                            "evidence_quote": "A:OBC进入慢充状态时的初始水温（CDU_RealityTemp）",
                        },
                        {
                            "symbol": "B",
                            "source_definition": "B:OBC进入慢充状态后的实时水温（CDU_RealityTemp）",
                            "physical_meaning": "OBC进入慢充状态后的实时水温",
                            "effective_timing": "OBC进入慢充状态后",
                            "unit": "℃",
                            "applies_to": "CDU_RealityTemp",
                            "calculation_usage": "与A组成水温差值",
                            "evidence_quote": "B:OBC进入慢充状态后的实时水温（CDU_RealityTemp）",
                        },
                    ],
                    "state_phases": [],
                    "control_branches": [],
                    "open_questions": [],
                },
                raw_text="{}",
            )
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n- 来源证据：current_prompt\n\n"
            "## 原文事实表\n- 源文定义 A 和 B。\n\n"
            "## 术语与变量定义\n"
            "- A/B：当初始水温与实时水温的差值（|B-A|）变化时参与控制。\n\n"
            "## 需求理解\n- 使用 |B-A| 表达水温差。\n\n"
            "## 条件与状态机\n"
            "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 运行后 | 进入运行后 | 对象 | 使用 |B-A| 调节 | 完成后 | current_prompt |\n"
            "| 完成后 | 完成后 | 对象 | 保持 | 退出 | current_prompt |\n\n"
            "## 歧义与待确认\n- 当前无。\n\n"
            "## 关键假设\n- 当前无。\n\n"
            "## 风险与边界\n- 当前无。\n\n"
            "## 验收建议\n- 覆盖阶段。\n\n"
            "## 证据引用\n- source: current_prompt\n"
        )
        return LLMResult(
            call_id=611,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    source = client.post(
        "/api/personal/sources/text",
        json={"title": "OBC需求", "content": "A:OBC进入慢充状态时的初始水温（CDU_RealityTemp）；B:OBC进入慢充状态后的实时水温（CDU_RealityTemp）；进入运行后按差值调节；完成后保持。"},
    )
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    _assert_quality_failed_draft(response, "语义骨架")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_rejects_control_branches_without_state_precondition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        if purpose == "personal_source_semantic_model":
            return LLMResult(
                call_id=620,
                provider="fake-test",
                model="semantic-fixture",
                status="ok",
                parsed={
                    "defined_terms": [],
                    "state_phases": [
                        {"phase_name": "运行后", "state_precondition": "进入运行状态后", "entry_condition": "进入运行状态", "in_phase_condition": "按温度分支", "exit_or_completion": "完成后", "post_completion_handling": "", "evidence_quote": "进入运行状态后按温度控制"}
                    ],
                    "control_branches": [
                        {"state_precondition": "进入运行状态后", "controlled_object": "执行器", "secondary_condition": "温度<15℃", "output_strategy": "低温策略", "exit_or_completion": "完成后", "evidence_quote": "进入运行状态后温度<15℃执行低温策略"}
                    ],
                    "open_questions": [],
                },
                raw_text="{}",
            )
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n- 来源证据：current_prompt\n\n"
            "## 原文事实表\n- 源文包含状态前置和温度分支。\n\n"
            "## 术语与变量定义\n- 当前无短变量。\n\n"
            "## 需求理解\n- 根据温度区分策略。\n\n"
            "## 条件与状态机\n"
            "| 阶段 | 进入条件 | 适用对象 | 控制策略 | 退出/完成条件 | 证据引用 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 温度<15℃ | 温度<15℃ | 执行器 | 低温策略 | 完成后 | current_prompt |\n\n"
            "## 歧义与待确认\n- 当前无。\n\n"
            "## 关键假设\n- 当前无。\n\n"
            "## 风险与边界\n- 当前无。\n\n"
            "## 验收建议\n- 覆盖温度。\n\n"
            "## 证据引用\n- source: current_prompt\n"
        )
        return LLMResult(
            call_id=621,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    source = client.post(
        "/api/personal/sources/text",
        json={"title": "通用需求", "content": "进入运行状态后，若温度<15℃执行低温策略；完成后保持。"},
    )
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    _assert_quality_failed_draft(response, "状态前置")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_requirement_analysis_report_rejects_mixed_lifecycle_phases_for_generic_input(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        content = (
            "# 需求分析报告\n\n"
            "## 输入摘要\n- 来源证据：current_prompt\n\n"
            "## 原文事实表\n- 源文包含 T1/T2 定义，以及故障后和恢复后两个阶段。\n\n"
            "## 术语与变量定义\n"
            "- T1：故障检测开始时间。\n"
            "- T2：恢复确认时间。\n\n"
            "## 需求理解\n- 设备需要处理故障和恢复。\n\n"
            "## 条件与状态机\n"
            "- 系统根据 T1/T2 统一处理设备状态变化。\n\n"
            "## 歧义与待确认\n- 当前无。\n\n"
            "## 关键假设\n- 当前无。\n\n"
            "## 风险与边界\n- 当前无。\n\n"
            "## 验收建议\n- 覆盖状态变化。\n\n"
            "## 证据引用\n- source: current_prompt\n"
        )
        return LLMResult(
            call_id=503,
            provider="fake-test",
            model="analysis-fixture",
            status="ok",
            parsed={"title": "通用状态需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    source = client.post(
        "/api/personal/sources/text",
        json={"title": "通用需求", "content": "T1:故障检测开始时间；T2:恢复确认时间；设备故障后进入保护模式；设备恢复后退出保护模式。"},
    )
    assert source.status_code == 200

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report", "source_uids": [source.json()["source_uid"]]},
    )

    _assert_quality_failed_draft(response, "阶段")
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 1


def test_directional_revision_uses_llm_template_quality_and_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    calls: list[dict[str, str]] = []

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        calls.append({"purpose": purpose, "system_prompt": system_prompt, "user_prompt": user_prompt})
        if purpose == "personal_artifact_generate":
            content = _valid_requirement_analysis_content()
            call_id = 221
        else:
            content = _valid_requirement_analysis_content("已按方向性意见重组，整体更像功能规范，减少实现细节，补充边界条件和验收标准。")
            call_id = 222
        return LLMResult(
            call_id=call_id,
            provider="fake-test",
            model="revision-fixture",
            status="ok",
            parsed={"title": "需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    created = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report"},
    )
    assert created.status_code == 200
    draft_uid = created.json()["draft_uid"]

    revised = client.post(
        f"/api/personal/drafts/{draft_uid}/revise",
        json={"feedback": "整体更像功能规范，减少实现细节，补充边界条件和验收标准"},
    )
    assert revised.status_code == 200
    payload = revised.json()
    assert payload["current_revision"] == 2
    assert "修订说明" not in payload["content"]
    assert "已按方向性意见重组" in payload["content"]
    assert [item["purpose"] for item in calls] == ["personal_artifact_generate", "personal_artifact_revise"]
    revision_prompt = calls[-1]["user_prompt"]
    assert '"mode": "revise_existing_draft"' in revision_prompt
    assert "整体更像功能规范" in revision_prompt
    assert "## 输入摘要" in revision_prompt
    generation = payload["metadata"]["generation"]
    assert generation["phase"] == "phase4_document_artifact_revision"
    assert generation["feedback"].startswith("整体更像功能规范")
    assert generation["previous_revision"] == 1
    assert generation["skill"]["name"] == "requirement-analysis-report"
    assert generation["template"]["hash"]
    assert generation["quality"]["passed"] is True
    assert generation["evidence_refs"]
    assert generation["llm"]["call_id"] == 222

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_draft_revisions WHERE draft_uid=?", (draft_uid,)).fetchone()[0] == 2


def test_directional_revision_quality_failure_creates_no_revision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")

    def fake_complete_json(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        content = _valid_requirement_analysis_content()
        if purpose == "personal_artifact_revise":
            content = "# 需求分析报告\n\n## 输入摘要\n- {{input_summary}}\n\n## 需求理解\n- 缺少其他 required sections。\n\n## 证据引用\n- source: current_prompt\n"
        return LLMResult(
            call_id=331 if purpose == "personal_artifact_generate" else 332,
            provider="fake-test",
            model="revision-quality-fixture",
            status="ok",
            parsed={"title": "需求分析报告", "content_format": "markdown", "content": content},
            raw_text="{}",
        )

    monkeypatch.setattr(LLMBridge, "complete_json", fake_complete_json)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    created = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report"},
    )
    assert created.status_code == 200
    draft_uid = created.json()["draft_uid"]

    revised = client.post(f"/api/personal/drafts/{draft_uid}/revise", json={"feedback": "改成很短"})
    failed = _assert_quality_failed_draft(revised, "required_sections")
    assert failed["status"] == "quality_failed"
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_draft_revisions WHERE draft_uid=?", (draft_uid,)).fetchone()[0] == 2


def test_lineage_uses_active_upstream_and_excludes_quality_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    session_uid = client.post("/api/personal/chat/turn", json={"content": "创建会话"}).json()["session"]["session_uid"]

    requirement = client.post(
        "/api/personal/drafts",
        json={"document_type": "requirement_analysis_report", "session_uid": session_uid, "title": "需求分析", "content": "# 需求分析"},
    ).json()
    created_failed = client.post(
        "/api/personal/drafts",
        json={"document_type": "requirement_breakdown", "session_uid": session_uid, "title": "旧拆解", "content": "# 旧拆解"},
    ).json()
    client.post(
        f"/api/personal/drafts/{created_failed['draft_uid']}/revise-manual",
        json={"content": "# 旧拆解失败", "make_active": True, "metadata": {"m": 1}},
    )
    with connect(db_path) as conn:
        conn.execute("UPDATE personal_drafts SET status='quality_failed', is_active=0 WHERE draft_uid=?", (created_failed["draft_uid"],))

    breakdown = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求拆解", "document_type": "requirement_breakdown", "session_uid": session_uid},
    )
    assert breakdown.status_code == 200, breakdown.json()
    breakdown_payload = breakdown.json()
    assert breakdown_payload["derived_from_draft_uid"] == requirement["draft_uid"]
    upstream = breakdown_payload["metadata"]["generation"]["evidence_refs"]
    assert requirement["draft_uid"] == breakdown_payload["derived_from_draft_uid"]
    assert upstream["active_source_uids"] == []


def test_reactivating_upstream_marks_downstream_lineage_stale(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))
    first_turn = client.post("/api/personal/chat/turn", json={"content": "创建会话"}).json()
    session_uid = first_turn["session"]["session_uid"]

    requirement = client.post(
        "/api/personal/drafts",
        json={"document_type": "requirement_analysis_report", "session_uid": session_uid, "title": "需求", "content": "# 需求"},
    ).json()
    breakdown = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求拆解", "document_type": "requirement_breakdown", "session_uid": session_uid},
    ).json()
    assert breakdown["derived_from_draft_uid"] == requirement["draft_uid"]
    assert breakdown["lineage_stale"] is False

    revised = client.post(
        f"/api/personal/drafts/{requirement['draft_uid']}/revise-manual",
        json={"content": "# 需求 v2", "make_active": True},
    )
    assert revised.status_code == 200

    refreshed = client.get(f"/api/personal/drafts/{breakdown['draft_uid']}").json()
    assert refreshed["lineage_stale"] is True


def test_document_generation_requires_configured_llm_and_creates_no_draft(tmp_path: Path, monkeypatch) -> None:
    for key in [
        "PERSONAL_AGENT_LLM_PROVIDER",
        "PERSONAL_AGENT_LLM_MODEL",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "OPENROUTER_API_KEY",
        "XAI_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    def fail_not_configured(self: Any, *, purpose: str, system_prompt: str, user_prompt: str, project_id: int | None = None, task_uid: str = "") -> Any:
        raise LLMError("LLM_NOT_CONFIGURED", "No personal provider configured.")

    monkeypatch.setattr(LLMBridge, "complete_json", fail_not_configured)
    db_path = tmp_path / "agent.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(create_personal_app(db_path, workspace))

    response = client.post(
        "/api/personal/documents/propose",
        json={"prompt": "生成需求分析报告", "document_type": "requirement_analysis_report"},
    )
    assert response.status_code == 400
    assert "LLM" in response.json()["detail"]
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_drafts").fetchone()[0] == 0
