from __future__ import annotations

import json
import re
from typing import Any


def fake_completion(purpose: str, user_prompt: str) -> dict[str, Any]:
    """Deterministic local-test fixture; production intent judgement must use a configured LLM."""
    if purpose == "personal_source_semantic_model":
        try:
            payload = json.loads(user_prompt)
        except Exception:
            payload = {}
        sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
        source = sources[0] if sources and isinstance(sources[0], dict) else {}
        text = str(source.get("plain_text") or "")
        defined_terms = _fake_defined_terms(text)
        state_phases = _fake_state_phases(text)
        control_branches = _fake_control_branches(text, state_phases)
        return {
            "defined_terms": defined_terms,
            "state_phases": state_phases,
            "control_branches": control_branches,
            "open_questions": [],
        }
    if purpose == "personal_skill_reflect":
        try:
            payload = json.loads(user_prompt)
        except Exception:
            payload = {}
        message = str(payload.get("user_message") or "")
        compact = re.sub(r"\s+", "", message).lower()
        if any(token in compact for token in ["批准这个skill修改", "批准这个skill更新", "记住这个生成方式"]):
            return {"has_skill_update_signal": False, "approval_intent": "approve_latest", "confidence": 0.93}
        if any(token in compact for token in ["驳回刚才那个skill更新", "不要改这个skill", "驳回这个skill修改"]):
            return {"has_skill_update_signal": False, "approval_intent": "reject_latest", "confidence": 0.93}
        if "功能规范" in message and ("不要写实现细节" in message or "不写实现细节" in message):
            return {
                "has_skill_update_signal": True,
                "approval_intent": "none",
                "confidence": 0.88,
                "target_skill": "functional-spec",
                "change_type": "instruction_patch",
                "reason": "用户要求后续功能规范不要写实现细节。",
                "proposed_change": "## Instructions\n- 功能规范只描述用户可观察行为、边界、输入输出和验收标准，不写函数、变量、内部算法、patch、diff 或代码级实现细节。",
                "risk": "会减少设计细节，但符合功能规范边界。",
            }
        return {"has_skill_update_signal": False, "approval_intent": "none", "confidence": 0.2}
    if purpose == "personal_learning_reflect":
        try:
            payload = json.loads(user_prompt)
        except Exception:
            payload = {}
        message = str(payload.get("user_message") or "")
        compact = re.sub(r"\s+", "", message).lower()

        approve_latest = any(token in compact for token in ["批准这条经验", "批准刚才那条", "记住刚才那条", "这条以后都用", "approve latest"])
        reject_latest = any(token in compact for token in ["驳回刚才那条", "驳回这条经验", "不要记刚才", "别记刚才", "reject latest"])
        if approve_latest or reject_latest:
            return {
                "has_learning_signal": False,
                "confidence": 0.93,
                "feedback_type": "memory_approval" if approve_latest else "memory_rejection",
                "scope": "project",
                "candidate_lesson": "",
                "anti_behavior": "",
                "approval_intent": "approve_latest" if approve_latest else "reject_latest",
                "reason": "用户在对话中要求审批最近的学习候选。",
            }

        signal_terms = [
            "以后",
            "下次",
            "不要固定模板",
            "按这种方式回答",
            "你理解错了",
            "刚才这样更好",
            "这个修改是对的",
            "以后都这样",
            "下次不要这样",
        ]
        has_signal = any(token in compact for token in signal_terms)
        if not has_signal:
            return {
                "has_learning_signal": False,
                "confidence": 0.18,
                "feedback_type": "none",
                "scope": "project",
                "candidate_lesson": "",
                "anti_behavior": "",
                "approval_intent": "none",
                "reason": "普通问题或一次性任务，不沉淀为经验。",
            }

        lesson_parts: list[str] = []
        anti_parts: list[str] = []
        feedback_type = "workflow_preference"
        if "有条理" in message or "条理" in message:
            lesson_parts.append("回答应更有条理，先给明确结论，再按问题自然组织关键理由和后续动作")
            feedback_type = "style_preference"
        if "不要固定模板" in message or "固定模板" in message or "不要模板" in message:
            lesson_parts.append("保持自然表达，不把回答写成僵硬固定模板")
            anti_parts.append("不要为了显得结构化而机械套用固定栏目或重复话术")
            feedback_type = "style_preference"
        if "功能规范" in message and ("不要写实现细节" in message or "不写实现细节" in message):
            lesson_parts.append("功能规范不要写实现细节，应聚焦用户可观察行为、边界、输入输出和验收标准")
            anti_parts.append("不要在功能规范中展开代码级实现、内部算法或函数细节")
            feedback_type = "workflow_preference"
        if "你理解错了" in message or "理解错" in message:
            lesson_parts.append("用户指出理解偏差时，应先修正意图理解，再继续处理任务")
            anti_parts.append("不要沿着已被用户纠正的错误理解继续回答")
            feedback_type = "correction"
        if "刚才这样更好" in message or "这个修改是对的" in message:
            lesson_parts.append("用户确认更好的处理方式时，应抽象为后续同类任务的质量偏好")
            feedback_type = "quality_bar"
        if "按这种方式回答" in message:
            lesson_parts.append("后续同类回答应遵守用户刚确认的表达方式，但只学习原则，不机械复刻格式")
            feedback_type = "style_preference"
        lesson = "；".join(dict.fromkeys(part for part in lesson_parts if part)) or "后续同类对话应遵守用户本轮提出的长期偏好、纠错意见或工作方式要求"
        anti_behavior = "；".join(dict.fromkeys(part for part in anti_parts if part))
        return {
            "has_learning_signal": True,
            "confidence": 0.88,
            "feedback_type": feedback_type,
            "scope": "global_personal" if "以后" in message or "下次" in message else "project",
            "candidate_lesson": lesson,
            "anti_behavior": anti_behavior,
            "approval_intent": "none",
            "reason": "用户表达了可复用的长期偏好、纠错或工作方式要求。",
        }
    if purpose == "personal_intent_route":
        try:
            payload = json.loads(user_prompt)
        except Exception:
            payload = {}
        message = str(payload.get("user_message") or "")
        lower = message.lower()
        active_sources = payload.get("active_sources") if isinstance(payload.get("active_sources"), list) else []
        active_draft = payload.get("active_draft") if isinstance(payload.get("active_draft"), dict) else {}

        intent = "answer_only"
        document_type = ""
        answer_mode = "general_chat"
        creates_draft = False
        revises_draft = False
        requires_source = False
        requires_draft = False
        requires_codebase = False
        requires_confirmation = False
        confidence = 0.82

        if any(token in lower for token in ["低置信", "low confidence", "uncertain-route"]):
            confidence = 0.2
            intent = "generate_document"
            document_type = "requirement_analysis_report"
            creates_draft = True
            requires_source = True
        elif any(token in lower for token in ["刚才草稿", "当前草稿", "这个草稿", "补充异常", "修订草稿", "revise draft"]):
            intent = "revise_draft"
            revises_draft = True
            requires_draft = True
            confidence = 0.9
        elif any(token in lower for token in ["生成", "写一份", "输出", "创建"]) and any(token in lower for token in ["报告", "文档", "需求分析", "需求拆解", "功能规范", "详细设计", "测试用例"]):
            intent = "generate_document"
            creates_draft = True
            requires_source = True
            confidence = 0.92
            if "需求拆解" in lower:
                document_type = "requirement_breakdown"
            elif "功能规范" in lower:
                document_type = "functional_spec"
            elif "详细设计" in lower:
                document_type = "detailed_design"
            elif "测试用例" in lower:
                document_type = "test_case_spec"
            else:
                document_type = "requirement_analysis_report"
        elif any(token in lower for token in ["分析", "识别", "当前需求", "需求资料", "输入材料", "输入资料"]):
            intent = "analyze_input_source"
            answer_mode = "input_source_analysis"
            requires_source = bool(active_sources)
            confidence = 0.88
        elif any(token in lower for token in ["改代码", "代码patch", "patch", "diff"]):
            intent = "propose_code_patch"
            requires_codebase = True
            requires_confirmation = True
            confidence = 0.86
        elif any(token in lower for token in ["跑测试", "运行测试", "pytest", "验证", "build"]):
            intent = "run_validation"
            requires_codebase = True
            requires_confirmation = True
            confidence = 0.86
        elif any(token in lower for token in ["记住", "以后", "下次", "经验", "不要再"]):
            intent = "learn_feedback"
            confidence = 0.84

        return {
            "intent": intent,
            "confidence": confidence,
            "target_document_type": document_type,
            "requires_active_source": requires_source,
            "requires_active_draft": requires_draft or bool(active_draft and intent == "revise_draft"),
            "requires_codebase": requires_codebase,
            "creates_draft": creates_draft,
            "revises_draft": revises_draft,
            "writes_project_files": False,
            "requires_user_confirmation": requires_confirmation,
            "answer_mode": answer_mode,
            "reason": "fake provider fixture for personal LLM semantic routing tests.",
        }
    if purpose == "personal_chat_answer":
        try:
            payload = json.loads(user_prompt)
        except Exception:
            payload = {}
        message = str(payload.get("user_message") or "")
        pending_memories = payload.get("pending_memory_candidates") if isinstance(payload.get("pending_memory_candidates"), list) else []
        memory_rules = []
        for item in pending_memories:
            if isinstance(item, dict):
                lesson = str(item.get("lesson") or item.get("expected_behavior") or "").strip()
                if lesson:
                    memory_rules.append(lesson)
        memory_prefix = ""
        if memory_rules:
            memory_prefix = "按当前会话待批准经验，我会保持更有条理且避免固定模板。\n\n"
        sources = payload.get("active_sources") if isinstance(payload.get("active_sources"), list) else []
        source = sources[0] if sources and isinstance(sources[0], dict) else {}
        source_uid = str(source.get("source_uid") or "")
        source_title = str(source.get("title") or "当前输入材料")
        excerpt = str(source.get("plain_text_excerpt") or "").strip()
        summary_lines = [line.strip(" \t-#*") for line in excerpt.splitlines() if line.strip()]
        summary = "；".join(summary_lines[:3]) if summary_lines else excerpt[:180]
        if source_uid:
            answer = (
                memory_prefix +
                f"基于 LLM 对《{source_title}》的理解，当前问题是：{message}\n\n"
                f"我识别到的核心材料线索：{summary or '材料内容较短，需要继续补充明确需求描述。'}\n\n"
                "下一步可以继续让我做两类事：一是继续追问和补充缺口，二是明确要求生成需求分析报告、需求拆解、功能规范、详细设计或测试用例规格。"
            )
            return {"answer": answer, "used_sources": [source_uid], "limitations": []}
        return {
            "answer": memory_prefix + "当前没有激活的输入材料。我可以先回答你的问题，但如果要做需求分析，请先粘贴或上传需求资料。",
            "used_sources": [],
            "limitations": ["no_active_source"],
        }
    if purpose in {"personal_artifact_generate", "personal_artifact_revise"}:
        try:
            payload = json.loads(user_prompt)
        except Exception:
            payload = {}
        artifact_type = str(payload.get("document_type") or payload.get("artifact_type") or "requirement_analysis_report")
        content_format = str(payload.get("content_format") or "markdown")
        sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
        current_draft = payload.get("current_draft") if isinstance(payload.get("current_draft"), dict) else {}
        feedback = str(payload.get("feedback") or "")
        memories = payload.get("memories") if isinstance(payload.get("memories"), list) else []
        session_memories = payload.get("session_memories") if isinstance(payload.get("session_memories"), list) else []
        source_uid = str((sources[0] or {}).get("source_uid") if sources and isinstance(sources[0], dict) else "current_prompt")
        immediate_rules: list[tuple[str, str]] = []
        for item in session_memories:
            if isinstance(item, dict):
                lesson = str(item.get("lesson") or item.get("expected_behavior") or "").strip()
                if lesson:
                    immediate_rules.append((lesson, str(item.get("id") or "")))
        long_term_rules: list[str] = []
        for item in memories:
            if isinstance(item, dict):
                content = str(item.get("content_excerpt") or item.get("content") or "").strip()
                if content:
                    long_term_rules.append(content)
        code_impact = payload.get("code_impact") if isinstance(payload.get("code_impact"), dict) else {}
        symbol_names = []
        for item in code_impact.get("candidate_symbols") or code_impact.get("symbols") or []:
            if isinstance(item, dict) and item.get("name"):
                symbol_names.append(str(item["name"]))
            elif isinstance(item, str):
                symbol_names.append(item)
        symbol_text = ", ".join(symbol_names[:5]) or "no symbol candidates"
        revision_line = f"- 本版已按方向性修改意见重组：{feedback}\n" if purpose == "personal_artifact_revise" and feedback else ""
        if artifact_type == "test_case_spec":
            return {
                "title": "测试用例规格",
                "content_format": "json_table",
                "content": {
                    "columns": ["id", "requirement_ref", "scenario", "precondition", "expected", "evidence"],
                    "rows": [
                        {"id": "TC-001", "requirement_ref": "REQ-001", "scenario": "normal 正常路径", "precondition": "输入满足触发条件", "expected": "输出满足需求目标", "evidence": source_uid},
                        {"id": "TC-002", "requirement_ref": "REQ-001", "scenario": "boundary 边界条件", "precondition": "输入接近边界值", "expected": "系统保持定义行为", "evidence": source_uid},
                        {"id": "TC-003", "requirement_ref": "REQ-001", "scenario": "exception 异常诊断", "precondition": "输入无效或依赖不可用", "expected": "系统进入可诊断失败处理路径", "evidence": source_uid},
                    ],
                    "boundary": "只生成测试用例规格，不生成测试代码",
                },
                "evidence_refs_used": {"source_uids": [source_uid]},
                "boundary_confirmation": {"personal_draft_only": True, "writes_formal_artifacts": False, "generates_code_patch": False, "uses_patch_apply": False},
            }
        if artifact_type == "trace_matrix":
            return {
                "title": "追溯矩阵",
                "content_format": "json_table",
                "content": {
                    "columns": ["source", "requirement", "artifact", "verification"],
                    "rows": [
                        {"source": source_uid, "requirement": "REQ-001", "artifact": "personal draft", "verification": "TC-001 normal"},
                        {"source": source_uid, "requirement": "REQ-001", "artifact": "personal draft revision", "verification": "review checklist"},
                    ],
                },
                "evidence_refs_used": {"source_uids": [source_uid]},
                "boundary_confirmation": {"personal_draft_only": True, "writes_formal_artifacts": False, "generates_code_patch": False, "uses_patch_apply": False},
            }
        if artifact_type == "functional_spec":
            content = (
                "# 功能规范\n\n"
                "## 功能目标\n"
                f"- 基于来源证据 {source_uid} 定义用户可观察的功能行为。\n\n"
                "## 用户可观察行为\n"
                "- 输入条件、输出结果、异常提示和验收路径均以用户可验证行为表达。\n\n"
                "## 输入与输出\n"
                "- 输入来自当前需求材料或本轮用户描述；输出为用户可验证的功能结果、提示或状态变化。\n\n"
                "## 状态与异常场景\n"
                "- 覆盖正常状态、边界状态和异常输入下的外部可观察响应。\n\n"
                "## 非目标\n"
                "- 不包含代码级实现、具体函数逻辑、内部算法、可应用补丁或正式发布动作。\n\n"
                "## 验收标准\n"
                "- 评审时可依据输入、输出、异常响应和证据引用逐项确认。\n"
                + revision_line
                + "\n"
                + ("## 长期经验\n" + "".join(f"- 长期经验：{rule}\n" for rule in long_term_rules) + "\n" if long_term_rules else "")
                + (
                    "## 行为规则注入\n"
                    + "".join(f"- 本会话即时遵守：{rule}" + (f"（candidate:{candidate_id}）" if candidate_id else "") + "\n" for rule, candidate_id in immediate_rules)
                    + "\n"
                    if immediate_rules
                    else ""
                )
                +
                "## 证据引用\n"
                f"- source: {source_uid}\n"
            )
        elif artifact_type == "requirement_analysis_report":
            semantic_model = payload.get("source_semantic_model") if isinstance(payload.get("source_semantic_model"), dict) else {}
            defined_terms = semantic_model.get("defined_terms") if isinstance(semantic_model.get("defined_terms"), list) else []
            control_branches = semantic_model.get("control_branches") if isinstance(semantic_model.get("control_branches"), list) else []
            source_excerpt = str((sources[0] or {}).get("plain_text") if sources and isinstance(sources[0], dict) else "").strip()
            source_facts = [
                f"- 来源材料包含待分析需求：{source_excerpt[:180] or '当前提示'}。",
                "- 需求分析需保留源文中的定义关系、阶段前置条件、控制对象和输出策略。",
            ]
            term_lines = []
            for item in defined_terms:
                if isinstance(item, dict) and item.get("symbol"):
                    term_lines.append(
                        f"- {item.get('symbol')}：{item.get('source_definition')}；物理含义：{item.get('physical_meaning')}；"
                        f"生效/采样时刻：{item.get('effective_timing')}；单位：{item.get('unit') or '未提供'}；"
                        f"适用对象：{item.get('applies_to')}；计算用途：{item.get('calculation_usage')}。"
                    )
            if not term_lines:
                term_lines = [
                    "- 当前源文未抽取到显式短变量定义；若源文存在定义关系，需要在本节保留。",
                ]
            branch_lines = []
            for item in control_branches:
                if isinstance(item, dict) and item.get("state_precondition"):
                    branch_lines.append(
                        f"| {item.get('state_precondition')} | {item.get('state_precondition')} | {item.get('controlled_object')} | "
                        f"{item.get('secondary_condition')} | {item.get('output_strategy')} | {item.get('exit_or_completion')} | {source_uid} |"
                    )
            if not branch_lines:
                branch_lines = [
                    f"| 源文阶段 | 源文状态前置 | 源文控制对象 | 源文二级条件 | 源文输出策略 | 源文退出/完成条件 | {source_uid} |",
                ]
            ambiguities = [
                "- 若源文存在拼写、符号或状态值歧义，需要保留原文并待确认。",
                "- 若语义骨架无法抽取完整变量定义或状态前置条件，需要回到源文补充证据。",
            ]
            content = (
                "# 需求分析报告\n\n"
                "## 输入摘要\n"
                f"- 来源证据：{source_uid}\n"
                "- 文档目标：分析输入需求的事实、变量定义、状态条件、控制策略边界和验收线索。\n\n"
                "## 原文事实表\n"
                + "".join(f"- {line}\n" for line in source_facts)
                + "\n"
                "## 术语与变量定义\n"
                + "\n".join(term_lines)
                + "\n"
                "## 需求理解\n"
                "- 本需求需要优先保持源文定义和状态前置条件，再组织控制策略说明。\n"
                "- 需求分析聚焦外部可观察行为，不补写实现代码或控制算法细节。\n\n"
                "## 条件与状态机\n"
                "| 阶段 | 状态判定条件 | 控制对象 | 二级条件 | 输出策略 | 退出/完成条件 | 证据引用 |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                + "\n".join(branch_lines)
                + "\n\n"
                "## 歧义与待确认\n"
                + "".join(f"{line}\n" for line in ambiguities)
                + "\n"
                "## 关键假设\n"
                "- 未明确给出的状态机抖动处理、信号消抖和异常降级策略，暂作为后续澄清项。\n\n"
                "## 风险与边界\n"
                "- 温度边界值（15℃、37℃、40℃、60℃、20℃）需要精确定义夹紧和归属规则。\n"
                "- 传感器异常、状态信号不一致和线性区间外输入需要在后续设计阶段补全处理策略。\n\n"
                "## 验收建议\n"
                "- 需要按状态分支、边界值、完成后 10 分钟逻辑以及信号组合变化进行验收。\n"
                "- 后续功能规范与测试用例应基于原文事实表，不得将待确认项直接当作已确认事实。\n\n"
                "## 证据引用\n"
                f"- source: {source_uid}\n"
            )
        elif artifact_type == "detailed_design":
            content = (
                "# 软件详细设计\n\n"
                "## 模块职责\n"
                "- 基于需求输入划分职责、接口和状态边界。\n\n"
                "## 接口与数据\n"
                "- 接口和数据流需要绑定代码证据后细化。\n\n"
                "## 状态与错误处理\n"
                "- 无效输入、依赖不可用和默认值策略需要与调用方可观察行为保持一致。\n\n"
                "## Codebase Impact 证据\n"
                f"- impact_analyze.passed: {bool(code_impact.get('passed'))}\n"
                f"- symbols: {symbol_text}\n\n"
                "## Symbol Lookup 证据\n"
                f"- {symbol_text}\n\n"
                "## Call Graph 证据\n"
                "- call graph evidence carried from context when available\n\n"
                "## Include Impact 证据\n"
                "- include impact evidence carried from context when available\n\n"
                "## Macro / Type / Variable 证据\n"
                "- macro/type/variable evidence carried from context when available\n\n"
                "## 代码影响上下文\n"
                "- 设计判断必须回到上述代码影响、符号、调用、include 以及宏/类型/变量证据。\n\n"
                "## 安全边界\n"
                "- 本草稿只描述设计，不生成 C 代码 patch，不生成可应用代码补丁。\n"
            )
        elif artifact_type == "requirement_breakdown":
            content = (
                "# 需求拆解\n\n"
                "## 候选需求条目\n"
                f"1. REQ-001: 基于来源证据 {source_uid} 定义单一职责需求。\n\n"
                "## 触发条件\n"
                "- 由输入材料描述的用户动作、系统状态或外部事件触发。\n\n"
                "## 预期行为\n"
                "- 系统产生可验证、可追溯的外部行为。\n\n"
                "## 验收标准\n"
                "- 每条需求需要可验证、可追溯、可评审。\n\n"
                "## 证据引用\n"
                f"- source: {source_uid}\n"
            )
        else:
            content = (
                "# 需求分析报告\n\n"
                "## 输入摘要\n"
                f"- 来源证据：{source_uid}\n\n"
                "## 需求理解\n"
                "- 识别功能目标、约束、异常场景和验收线索。\n"
                + revision_line
                + "\n"
                "## 关键假设\n"
                "- 当前草稿仅基于本轮输入材料，未确认的信息标记为待澄清。\n\n"
                "## 风险与边界\n"
                "- 本结果仅写入 personal draft，不进入正式发布记录。\n\n"
                "## 待澄清问题\n"
                "- 需要确认边界条件、异常处理和验收口径。\n\n"
                "## 验收建议\n"
                "- 基于来源证据逐项评审输入、输出、异常和边界覆盖。\n\n"
                "## 证据引用\n"
                f"- source: {source_uid}\n"
            )
        return {
            "title": str(current_draft.get("title") or "") or {
                "requirement_analysis_report": "需求分析报告",
                "requirement_breakdown": "需求拆解",
                "functional_spec": "功能规范",
                "detailed_design": "软件详细设计",
            }.get(artifact_type, artifact_type),
            "content_format": content_format,
            "content": content,
            "evidence_refs_used": {"source_uids": [source_uid]},
            "boundary_confirmation": {"personal_draft_only": True, "writes_formal_artifacts": False, "generates_code_patch": False, "uses_patch_apply": False},
        }
    question = _extract_user_message(user_prompt)
    intent = _semantic_intent(question)
    answer = _fake_answer_for_intent(intent, user_prompt)
    return {
        "intent": intent,
        "confidence": 0.86,
        "needs_clarification": False,
        "clarifying_question": "",
        "answer": answer,
            "tool_plan": [
            {"tool": "context_builder", "reason": "读取项目、需求、产物、质量检查、追溯、知识库和历史对话"},
            {"tool": "evidence_grounded_answer", "reason": "基于上下文生成中文回答"},
        ],
        "suggested_actions": ["查看需求级流程", "补齐缺失产物", "运行检查", "提交评审"],
        "knowledge_refs_used": "auto",
        "evidence_refs_used": "auto",
        "knowledge_code_claim_check": {
            "has_knowledge_or_code_claim": intent in {"knowledge", "code_and_tests"},
            "claim_types": ["knowledge_or_code_source"] if intent in {"knowledge", "code_and_tests"} else [],
            "evidence_sufficient": True,
            "requires_repair": False,
            "safe_answer": "",
            "rationale": "fake provider returns the self-check in the conversation JSON so tests do not need a second LLM call.",
        },
    }


def _extract_user_message(prompt: str) -> str:
    try:
        payload = json.loads(prompt)
        message = payload.get("user_message")
        if isinstance(message, str):
            return message
    except Exception:
        pass
    question_marker = "问题："
    context_marker = "\n平台上下文摘要："
    question_index = prompt.find(question_marker)
    if question_index >= 0:
        start = question_index + len(question_marker)
        end = prompt.find(context_marker, start)
        if end < 0:
            end = len(prompt)
        return prompt[start:end].strip()
    marker = '"user_message"'
    index = prompt.find(marker)
    if index < 0:
        return prompt[-1000:]
    return prompt[index : index + 1200]


def _extract_llm_status_from_prompt(prompt: str) -> dict[str, str]:
    match = re.search(r"当前LLM：provider=([^，\n]+)，model=([^，\n]+)，fake_provider=([^\n]+)", prompt)
    if not match:
        return {}
    return {
        "provider": match.group(1).strip(),
        "model": match.group(2).strip(),
        "fake_provider": match.group(3).strip(),
    }


def _semantic_intent(question: str) -> str:
    # Test fixture boundary: this token-based helper is reachable only from
    # _fake_completion after PERSONAL_AGENT_LLM_PROVIDER=fake is set explicitly.
    # Production routing and answering must not call it.
    text = question.lower()
    if any(token in text for token in ["重复回答", "固定答复", "固定回答", "套话", "智力退化", "降智", "退化", "repeat", "boilerplate"]):
        return "conversation_quality"
    if any(token in text for token in ["什么模型", "哪个模型", "模型是什么", "llm", "model", "provider", "fake provider"]):
        return "model_identity"
    if any(token in text for token in ["psl", "problem-solving", "problem solving", "问题解决闭环", "问题解决循环"]):
        return "psl_explanation"
    if any(token in text for token in ["自主完成", "自主推进", "自己完成", "自己推进", "自动完成", "能不能完成任务", "可以完成任务", "自己判断", "自行判断", "你决定"]):
        return "autonomous_capability"
    asks_not_for_location = any(token in text for token in ["不是问文件", "不是文件", "不是要看产物", "不要打开产物", "not asking for file", "not file"])
    asks_process_blocker = any(token in text for token in ["流程卡点", "过程域", "阻塞", "卡在", "卡点"])
    if any(token in text for token in ["知识", "模板", "经验", "规范", "学习", "knowledge", "template", "ÖªÊ¶", "Ä£°å", "¾­Ñé"]):
        return "knowledge"
    if any(token in text for token in ["受控版本", "评审", "批准", "review", "release"]):
        return "review_release"
    if any(token in text for token in ["闭环", "追溯", "覆盖", "链路", "trace"]):
        return "traceability"
    if any(token in text for token in ["代码", "c ", "c代码", ".c", "测试", "单元", "实现", "code", "test", "´úÂë", "²âÊÔ", "µ¥Ôª", "ÊµÏÖ"]):
        return "code_and_tests"
    if any(token in text for token in ["quality check", "质量", "门禁", "通过", "finding"]):
        return "check_quality"
    if not asks_not_for_location and any(token in text for token in ["文件", "哪里", "路径", "打开", "产物", "输出", "ÎÄ¼þ", "ÄÄ¸ö", "Â·¾¶", "²úÎï", "Êä³ö"]):
        return "locate_artifact"
    if asks_process_blocker or any(token in text for token in ["流程", "阶段", "进度", "下一步", "状态", "缺什么", "缺少"]):
        return "process_status"
    return "task_status"


def _fake_answer_for_intent(intent: str, prompt: str) -> str:
    if intent == "conversation_quality":
        return (
            "如果你看到连续问题都返回同一句话，通常不是项目证据本身给出了同一个结论，而是对话层退回到了 fake provider 或兜底模板。"
            "当前修复后的测试模式会把普通工程问题转交给证据型回答器，直接引用任务、草稿、检查记录和知识库记录；"
            "只有确实缺少可判断信号时，才会说明证据不足。"
        )
    if intent == "model_identity":
        llm_status = _extract_llm_status_from_prompt(prompt)
        provider = llm_status.get("provider") or "fake"
        model = llm_status.get("model") or "personal-fake-semantic-fixture"
        fake_provider = llm_status.get("fake_provider", "").lower() == "true" or provider == "fake"
        return (
            f"当前对话层接入的是 `provider={provider}` / `model={model}`。"
            + (
                "这不是正式推理模型，而是本地确定性测试夹具；所以当前 Agent 不是全程由真实 LLM 理解意图并执行。"
                if fake_provider
                else "这是当前配置中的正式 LLM provider；但 Agent 仍不是纯 LLM 直连执行器。"
            )
            + "PersonalAgent 的任务规划和对话会经过 PersonalLLMGateway；目标跟踪、资源检查、失败归因、受控动作、检查结论和证据型问答，都是规则、数据库和工具链共同组成的工作流。"
        )
    if intent == "psl_explanation":
        return (
            "问题解决循环负责维护持续目标、拆解可执行步骤、读取项目资源、调用受控动作、"
            "记录失败归因和证据结果；受控动作层负责把这些动作限制在草稿、检查和版本边界内。"
        )
    if intent == "autonomous_capability":
        return (
            "可以，但这里的“自主完成”不是无边界自动乱改。系统会先维护目标和步骤，再通过受控动作层选择动作；"
            "高风险结果只生成候选产物并进入检查与人工评审，不能直接绕过评审写入正式版本。"
        )
    if intent == "review_release":
        return "基于当前上下文，评审任务和版本状态需要同时查看：如果复核已批准且检查通过，才建议纳入受控版本；否则应先处理评审意见。"
    if intent == "knowledge":
        return "我会把命中的知识库条目作为过程模板、规则或历史经验引用到本次回答中；若命中为空，需要先导入模板、规范或经验文档。"
    if intent == "code_and_tests":
        return "当前回答会检查代码产物、单元测试结果和质量证据；不能只说生成了代码，必须能回到具体产物和验证记录。"
    if intent == "check_quality":
        return "质量结论必须来自检查记录：当前通过时可以推进复核，未通过时需要按问题修复并重新验证。"
    if intent == "traceability":
        return "当前需求已闭环时，追溯需要至少覆盖需求到设计、代码、测试和证据四类链接；缺任一类都不能宣称闭环完成。"
    if intent == "locate_artifact":
        return "我会从当前需求关联的产物索引里定位文件，而不是猜路径；对应文档可在产物面板中打开查看正文。"
    if intent == "process_status":
        return "项目应按需求进入结构化流程，逐项查看系统节点、必需产物、审批状态、质量状态和下一步动作。"
    return "我会先解析你的真实意图，再结合项目、需求、产物、质量检查、追溯、复核和知识库证据回答；证据不足时会说明缺口。"


def _fake_defined_terms(text: str) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for match in re.finditer(r"(?:^|[；;\n\r。])\s*([A-Z][0-9]?)\s*[:：=]\s*([^；;\n\r。]{2,120})", text):
        symbol = match.group(1).strip()
        desc = match.group(2).strip()
        terms.append(
            {
                "symbol": symbol,
                "source_definition": f"{symbol}:{desc}",
                "physical_meaning": desc.split("（", 1)[0].strip(),
                "effective_timing": _extract_timing_hint(desc),
                "unit": _extract_unit_hint(desc),
                "applies_to": _extract_applies_hint(desc),
                "calculation_usage": _extract_usage_hint(text, symbol),
                "evidence_quote": f"{symbol}:{desc}",
            }
        )
    return terms[:6]


def _fake_state_phases(text: str) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    patterns = [
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}进入[\u4e00-\u9fffA-Za-z0-9_（）()]{1,24}后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}完成后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}故障后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}恢复后)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            phrase = match.group(1).strip()
            if phrase:
                phases.append(
                    {
                        "phase_name": phrase,
                        "state_precondition": phrase,
                        "entry_condition": phrase,
                        "in_phase_condition": "",
                        "exit_or_completion": _phase_exit_hint(phrase),
                        "post_completion_handling": "",
                        "evidence_quote": phrase,
                    }
                )
    return phases[:6]


def _fake_control_branches(text: str, phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preconditions = [str(item.get("state_precondition") or "") for item in phases if str(item.get("state_precondition") or "").strip()]
    if not preconditions:
        preconditions = _default_preconditions(text)
    objects = _extract_control_objects(text) or ["控制对象"]
    branches: list[dict[str, Any]] = []
    for precondition in preconditions[:3]:
        for obj in objects[:2]:
            branches.append(
                {
                    "state_precondition": precondition,
                    "controlled_object": obj,
                    "secondary_condition": _secondary_condition_hint(text),
                    "output_strategy": _output_strategy_hint(text),
                    "exit_or_completion": _phase_exit_hint(precondition),
                    "evidence_quote": precondition,
                }
            )
    return branches[:6]


def _extract_timing_hint(desc: str) -> str:
    for token in ["进入后", "进入时", "完成后", "恢复后", "故障后", "触发后"]:
        if token in desc:
            return token
    return ""


def _extract_unit_hint(desc: str) -> str:
    for token in ["℃", "L/min", "%", "min", "ms", "s"]:
        if token in desc:
            return token
    return ""


def _extract_applies_hint(desc: str) -> str:
    match = re.search(r"（([^）]+)）", desc)
    return match.group(1).strip() if match else ""


def _extract_usage_hint(text: str, symbol: str) -> str:
    if re.search(rf"\b{re.escape(symbol)}\b.*[-+|]|[-+|].*\b{re.escape(symbol)}\b", text):
        return "参与差值或变化量计算"
    if symbol in text:
        return "参与控制或判断"
    return ""


def _phase_exit_hint(phrase: str) -> str:
    if "完成后" in phrase:
        return "完成后进入下一阶段"
    if "故障后" in phrase or "恢复后" in phrase:
        return "状态迁移后退出当前阶段"
    return "状态变化后退出"


def _default_preconditions(text: str) -> list[str]:
    hints = [token for token in ["进入", "完成后", "故障后", "恢复后"] if token in text]
    return hints or ["当前状态"]


def _extract_control_objects(text: str) -> list[str]:
    matches = re.findall(r"([\u4e00-\u9fffA-Za-z0-9_]{1,16})(?:策略|控制|调节|保持)", text)
    return list(dict.fromkeys(item for item in matches if len(item) >= 2))[:6]


def _secondary_condition_hint(text: str) -> str:
    hints = [token for token in ["温度", "水温", "差值", "状态"] if token in text]
    return "、".join(hints) if hints else "源文条件"


def _output_strategy_hint(text: str) -> str:
    if "保持" in text:
        return "按源文保持策略执行"
    if "线性" in text:
        return "按源文线性调节"
    if "调节" in text:
        return "按源文条件调节"
    return "按源文策略输出"
