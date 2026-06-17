from __future__ import annotations

import json
import re
from typing import Any

from .content_guard import personal_forbidden_hits


DOCUMENT_FORMAT_BY_TYPE = {
    "requirement_analysis_report": "markdown",
    "requirement_breakdown": "markdown",
    "functional_spec": "markdown",
    "detailed_design": "markdown",
    "test_case_spec": "json_table",
}


def validate_generated_artifact(
    *,
    document_type: str = "",
    content_format: str,
    content: str,
    context: dict[str, Any],
    skill: dict[str, Any],
    llm_result: dict[str, Any],
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document_type = document_type.strip()
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []

    def add(name: str, passed: bool, message: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "message": message})
        if not passed:
            blocking.append(message)

    expected_format = DOCUMENT_FORMAT_BY_TYPE.get(document_type)
    stripped = content.strip()
    template = template or {}
    template_provided = bool(template)
    template_format = str(template.get("format") or "").strip()
    required_sections = [str(item).strip() for item in (template.get("required_sections") or []) if str(item).strip()]

    add("non_empty_content", bool(stripped), "生成内容不能为空")
    add("content_format_matches", content_format == expected_format, f"{document_type} 必须使用 {expected_format} 格式")
    if template_provided:
        add("template_loaded", bool(template.get("loaded")) and bool(template.get("hash")), "文档生成必须加载 Skill 默认模板")
        add("required_sections_present", _required_sections_present(stripped, content_format, required_sections), "生成内容必须包含模板声明的 required_sections")
        add("no_unresolved_placeholders", not _has_unresolved_placeholder(stripped), "生成内容不能残留 {{placeholder}} 占位符")
        add("content_format_matches_template", not template_format or _format_matches_template(content_format, template_format), "生成内容格式必须匹配模板格式")
    add("skill_matches_document", skill.get("document_type") == document_type, "skill document_type 必须匹配文档类型")
    add("llm_gateway_used", bool(llm_result.get("llm_call_id")), "必须通过 LLM JSON 网关生成内容")

    hits = personal_forbidden_hits(stripped)
    add("personal_language_clean", not hits, f"生成内容不能包含个人 Agent 禁用词：{', '.join(hits)}")
    add("personal_draft_only", not _has_forbidden_side_effect(stripped), "生成内容不能包含受控发布、patch_apply 或可应用 diff 指令")

    evidence_refs = context.get("evidence_refs") if isinstance(context.get("evidence_refs"), dict) else {}
    has_evidence = any(bool(value) for value in evidence_refs.values())
    if document_type in {"requirement_analysis_report", "requirement_breakdown", "functional_spec", "detailed_design"}:
        evidence_ok = has_evidence or _mentions_evidence(stripped)
        add("has_evidence_reference", evidence_ok, "文档必须引用输入资料、知识、记忆或代码证据")
        add("evidence_policy_satisfied", evidence_ok, "文档必须满足证据引用策略")
    else:
        add("evidence_policy_satisfied", True, "文档满足证据引用策略")

    if document_type == "requirement_analysis_report":
        add("evidence_refs_consistent", _evidence_refs_consistent(stripped, context), "需求分析报告不能引用未由上下文提供的代码文件、接口或模块证据")
        add("source_ambiguity_preserved", _source_ambiguity_preserved(stripped, context), "源文档中的疑似拼写、状态值或符号歧义必须保留原文或列入待确认")
        add("analysis_inference_separated", _analysis_inference_separated(stripped), "需求分析报告必须将原文事实与分析推断分开")
        add("term_definitions_present", _markdown_section_present(stripped, "术语与变量定义"), "需求分析报告必须包含术语与变量定义章节")
        add("source_variable_definitions_preserved", _source_variable_definitions_preserved(stripped, context), "源文档中的变量、缩写或符号定义必须在术语与变量定义中保留")
        add("short_variable_semantics_preserved", _short_variable_semantics_preserved(stripped, context), "源文档中的短变量或变更量必须在术语与变量定义中保留物理含义，不能只保留变量名")
        add("lifecycle_phases_separated", _lifecycle_phases_separated(stripped, context), "源文档中的多个阶段、触发事件或完成后处理必须在条件与状态机中拆开")
        add("defined_variable_usage_preserves_semantics", _defined_variable_usage_preserves_semantics(stripped, context), "需求分析报告必须保留语义骨架中的变量原文定义、物理含义和生效/采样时刻，不能只写差值或泛化表达")
        add("state_precondition_covers_control_branches", _state_precondition_covers_control_branches(stripped, context), "需求分析报告中的控制策略必须显式挂载到语义骨架中的状态前置条件下")

    if document_type == "functional_spec":
        add("no_implementation_details", not _has_implementation_detail(stripped), "功能规范不能包含代码级实现、patch、diff、函数实现或内部算法细节")
    elif document_type == "detailed_design":
        impact = context.get("impact") if isinstance(context.get("impact"), dict) else {}
        add("contains_codebase_evidence_section", _contains_all(stripped, ["Codebase Impact", "Symbol Lookup"]), "详细设计必须包含代码证据章节")
        add("context_has_code_evidence", bool(impact), "详细设计必须带入 code impact 上下文")
    elif document_type == "test_case_spec":
        table = _json_table(stripped)
        add("valid_json_table", table is not None, "测试用例规格必须是包含 columns/rows 的 JSON 表格")
        if table is not None:
            scenarios = json.dumps(table.get("rows", []), ensure_ascii=False).lower()
            add(
                "covers_normal_boundary_exception",
                _has_any(scenarios, ["normal", "正常"]) and _has_any(scenarios, ["boundary", "边界"]) and _has_any(scenarios, ["exception", "异常", "diagnostic", "错误"]),
                "测试用例规格必须覆盖正常、边界和异常场景",
            )

    passed = not blocking
    score = round(sum(1 for item in checks if item["passed"]) / max(1, len(checks)), 3)
    return {
        "passed": passed,
        "score": score,
        "checks": checks,
        "blocking_failures": blocking,
    }


def _json_table(content: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(content)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("columns"), list) or not isinstance(payload.get("rows"), list):
        return None
    return payload


def _format_matches_template(content_format: str, template_format: str) -> bool:
    if template_format == "json_table":
        return content_format == "json_table"
    if template_format == "markdown":
        return content_format == "markdown"
    return True


def _required_sections_present(content: str, content_format: str, sections: list[str]) -> bool:
    if not sections:
        return True
    if content_format == "json_table":
        table = _json_table(content)
        if table is None:
            return False
        serialized = json.dumps(table, ensure_ascii=False).lower()
        return all(section.lower() in serialized or section in table for section in sections)
    return all(_markdown_section_present(content, section) for section in sections)


def _markdown_section_present(content: str, section: str) -> bool:
    pattern = re.compile(r"^\s{0,3}#{1,6}\s+" + re.escape(section) + r"\s*$", re.IGNORECASE | re.MULTILINE)
    return bool(pattern.search(content))


def _has_unresolved_placeholder(content: str) -> bool:
    return bool(re.search(r"\{\{[^{}]+\}\}", content))


def _has_forbidden_side_effect(content: str) -> bool:
    lowered = content.lower()
    if "patch_apply" in lowered or "diff --git" in lowered:
        return True
    if re.search(r"\b(create|write|insert)\s+release\s+record\b", lowered):
        return True
    return False


def _has_implementation_detail(content: str) -> bool:
    lowered = content.lower()
    forbidden = ["diff --git", "#include", "return ", "void ", "int ", "static ", "typedef ", "struct ", "patch"]
    if any(token in lowered for token in forbidden):
        return True
    return bool(re.search(r"\b[a-zA-Z_][a-zA-Z0-9_]*\s*\([^)]*\)\s*\{", content))


def _mentions_evidence(content: str) -> bool:
    lowered = content.lower()
    return any(token in lowered for token in ["evidence", "source", "knowledge", "memory", "证据", "来源", "知识", "经验"])


def _contains_all(content: str, tokens: list[str]) -> bool:
    lowered = content.lower()
    return all(token.lower() in lowered for token in tokens)


def _has_any(content: str, tokens: list[str]) -> bool:
    return any(token.lower() in content for token in tokens)


def _evidence_refs_consistent(content: str, context: dict[str, Any]) -> bool:
    allowed = _allowed_evidence_tokens(context)
    suspicious = _referenced_code_tokens(content)
    if not suspicious:
        return True
    return all(_token_allowed(token, allowed) for token in suspicious)


def _allowed_evidence_tokens(context: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for source in context.get("sources") or []:
        if isinstance(source, dict):
            for key in ("source_uid", "title"):
                value = str(source.get(key) or "").strip()
                if value:
                    allowed.add(value.lower())
    evidence_refs = context.get("evidence_refs") if isinstance(context.get("evidence_refs"), dict) else {}
    for value in evidence_refs.values():
        if isinstance(value, list):
            allowed.update(str(item).lower() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            allowed.add(value.lower())
    impact = context.get("impact") if isinstance(context.get("impact"), dict) else {}
    for key in ("impacted_files", "files", "source_files", "header_files"):
        for item in impact.get(key) or []:
            if isinstance(item, dict):
                allowed.update(str(v).lower() for v in item.values() if isinstance(v, str) and v.strip())
            elif isinstance(item, str) and item.strip():
                allowed.add(item.lower())
    for key in ("symbols", "candidate_symbols"):
        for item in impact.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                allowed.add(str(item["name"]).lower())
            elif isinstance(item, str) and item.strip():
                allowed.add(item.lower())
    return allowed


def _referenced_code_tokens(content: str) -> list[str]:
    tokens: list[str] = []
    patterns = [
        r"`([^`\n]+\.(?:c|h|cpp|hpp|cc|py|ts|tsx|js|jsx))`",
        r"\b(?:src|include|app|lib|drivers|modules)/[A-Za-z0-9_./-]+\.(?:c|h|cpp|hpp|cc|py|ts|tsx|js|jsx)\b",
        r"\b[A-Za-z0-9_./-]+\.(?:c|h|cpp|hpp|cc)\b",
    ]
    for pattern in patterns:
        tokens.extend(match.strip() for match in re.findall(pattern, content, flags=re.IGNORECASE) if match.strip())
    return list(dict.fromkeys(tokens))


def _token_allowed(token: str, allowed: set[str]) -> bool:
    lowered = token.lower()
    basename = lowered.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return any(lowered in item or item in lowered or basename in item for item in allowed)


def _source_ambiguity_preserved(content: str, context: dict[str, Any]) -> bool:
    source_text = "\n".join(str(item.get("plain_text") or "") for item in context.get("sources") or [] if isinstance(item, dict))
    ambiguous = _source_ambiguity_tokens(source_text)
    if not ambiguous:
        return True
    lowered = content.lower()
    has_confirmation_section = "歧义与待确认" in content or "待澄清问题" in content
    return all(token.lower() in lowered for token in ambiguous) and has_confirmation_section


def _source_ambiguity_tokens(source_text: str) -> list[str]:
    tokens: list[str] = []
    candidates = ["Praking Charging", "ilde", "IB-A|"]
    for token in candidates:
        if token in source_text:
            tokens.append(token)
    tokens.extend(re.findall(r"\b\w*\|\w*\b|\|\w+[-_]\w+\|?|\b\w+[-_]\w+\|", source_text))
    known_status_typos = re.findall(r"\b(?:Praking|Pakring|Chargring|Charing|ilde|idel)\b(?:\s+\w+)?", source_text, flags=re.IGNORECASE)
    tokens.extend(item.strip() for item in known_status_typos if item.strip())
    return tokens


def _analysis_inference_separated(content: str) -> bool:
    fact_section = _markdown_section_body(content, "原文事实表")
    if not fact_section:
        return False
    cleaned = fact_section
    for phrase in [
        "以下为输入材料中明确给出的事实，未做任何分析推断：",
        "以下为输入材料中明确给出的事实，未做任何分析推断",
        "以下为原文事实，未做任何分析推断：",
        "以下为原文事实，未做任何分析推断",
        "未做任何分析推断",
        "不做任何分析推断",
    ]:
        cleaned = cleaned.replace(phrase, "")
    inference_terms = ["假设", "风险", "可能", "建议", "推断", "需确认", "待确认", "测试", "仿真", "误差", "降级"]
    return not any(term in cleaned for term in inference_terms)


def _source_variable_definitions_preserved(content: str, context: dict[str, Any]) -> bool:
    source_text = _source_text(context)
    definitions = _source_definition_relations(source_text)
    if not definitions:
        return True
    section = _markdown_section_body(content, "术语与变量定义")
    if not section:
        return False
    section_norm = _normalize_for_relation_match(section)
    for item in definitions:
        if item["name"].lower() not in section_norm:
            return False
        required_terms = [term for term in item["terms"] if term]
        if required_terms and not any(term.lower() in section_norm for term in required_terms):
            return False
    return True


def _short_variable_semantics_preserved(content: str, context: dict[str, Any]) -> bool:
    source_text = _source_text(context)
    variables = _source_short_variable_tokens(source_text)
    if not variables:
        return True
    section = _markdown_section_body(content, "术语与变量定义")
    if not section:
        return False
    section_norm = _normalize_for_relation_match(section)
    for variable in variables:
        if variable.lower() not in section_norm:
            return False
        if not _short_variable_has_semantic_context(section, variable):
            return False
    return True


def _defined_variable_usage_preserves_semantics(content: str, context: dict[str, Any]) -> bool:
    terms = _semantic_defined_terms(context)
    if not terms:
        return True
    section = _markdown_section_body(content, "术语与变量定义")
    if not section:
        return False
    section_norm = _normalize_for_relation_match(section)
    for term in terms:
        symbol = str(term.get("symbol") or "").strip()
        if not symbol:
            continue
        if symbol.lower() not in section_norm:
            return False
        term_line = _term_definition_line(section, symbol)
        if not term_line:
            return False
        if not _term_line_has_structured_semantics(term_line):
            return False
        if _term_line_symbol_extends_expected(term_line, symbol):
            continue
        term_line_norm = _normalize_for_relation_match(term_line or section)
        required_fields = [
            str(term.get("source_definition") or ""),
            str(term.get("physical_meaning") or ""),
            str(term.get("effective_timing") or ""),
        ]
        covered_required = sum(1 for field in required_fields if field and _has_semantic_field_coverage(field, term_line_norm))
        required_count = sum(1 for field in required_fields if field)
        if required_count and covered_required < min(2, required_count):
            return False
        usage = str(term.get("calculation_usage") or "")
        if usage and not (_has_semantic_field_coverage(usage, term_line_norm) or _term_line_has_calculation_cell(term_line)):
            return False
    return True


def _state_precondition_covers_control_branches(content: str, context: dict[str, Any]) -> bool:
    branches = _semantic_control_branches(context)
    if not branches:
        return True
    section = _markdown_section_body(content, "条件与状态机") or _markdown_section_body(content, "需求理解")
    if not section:
        return False
    section_norm = _normalize_for_relation_match(section)
    state_contexts = _semantic_state_contexts(section)
    covered = 0
    for branch in branches:
        state = str(branch.get("state_precondition") or "").strip()
        controlled_object = str(branch.get("controlled_object") or "").strip()
        output = str(branch.get("output_strategy") or "").strip()
        if not state:
            continue
        has_state = _has_meaningful_overlap(state, section_norm) or _state_precondition_equivalent(state, state_contexts)
        has_object = not controlled_object or _has_controlled_object_overlap(controlled_object, section_norm)
        has_output = not output or _has_meaningful_overlap(output, section_norm)
        if has_state and has_object and has_output:
            covered += 1
    required = sum(1 for item in branches if str(item.get("state_precondition") or "").strip())
    if required == 0:
        return True
    return covered >= required


def _semantic_state_contexts(section: str) -> list[str]:
    contexts: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or "|" in stripped:
            contexts.append(stripped)
    return contexts


def _state_precondition_equivalent(state: str, contexts: list[str]) -> bool:
    state_tokens = set(_semantic_state_tokens(state))
    if not state_tokens:
        return True
    for context in contexts:
        context_tokens = set(_semantic_state_tokens(context))
        if state_tokens <= context_tokens:
            return True
    return False


def _semantic_state_tokens(text: str) -> list[str]:
    compact = _normalize_for_relation_match(text)
    tokens: list[str] = []
    for token in ["进入", "后", "中", "进行", "运行", "状态", "完成", "结束", "退出"]:
        if token in compact:
            tokens.append(token)
    tokens.extend(re.findall(r"[A-Za-z][A-Za-z0-9_]{1,40}", text))
    return list(dict.fromkeys(tokens))


def _lifecycle_phases_separated(content: str, context: dict[str, Any]) -> bool:
    source_text = _source_text(context)
    phases = _source_phase_relations(source_text)
    if len(phases) < 2:
        return True
    section = _markdown_section_body(content, "条件与状态机")
    if not section:
        return False
    section_norm = _normalize_for_relation_match(section)
    unique_phase_terms = []
    for phase in phases:
        terms = [term for term in phase["terms"] if term]
        if any(term.lower() in section_norm for term in terms):
            key = "|".join(terms)
            if key not in unique_phase_terms:
                unique_phase_terms.append(key)
    has_stage_structure = any(token in section for token in ["阶段", "运行阶段", "状态机", "|", "进入条件", "完成条件", "触发"])
    return has_stage_structure and len(unique_phase_terms) >= min(2, len(phases))


def _markdown_section_body(content: str, title: str) -> str:
    lines = content.splitlines(keepends=True)
    target = title.strip().casefold()
    start = None
    start_level = 0
    heading_pattern = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
    for index, line in enumerate(lines):
        match = heading_pattern.match(line.rstrip("\r\n"))
        if not match:
            continue
        heading_title = match.group(2).strip().rstrip("#").strip().casefold()
        if heading_title == target:
            start = index + 1
            start_level = len(match.group(1))
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        match = heading_pattern.match(lines[index].rstrip("\r\n"))
        if match and len(match.group(1)) <= start_level:
            end = index
            break
    return "".join(lines[start:end])


def _source_text(context: dict[str, Any]) -> str:
    return "\n".join(str(item.get("plain_text") or "") for item in context.get("sources") or [] if isinstance(item, dict))


def _source_definition_relations(source_text: str) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for match in re.finditer(r"(?:^|[；;\n\r。])\s*([A-Za-z][A-Za-z0-9_]{0,12})\s*[:：=]\s*([^；;\n\r。]{2,120})", source_text):
        name = match.group(1).strip()
        desc = match.group(2).strip()
        if _looks_like_definition_description(desc):
            definitions.append({"name": name, "description": desc, "terms": _definition_terms(desc)})
    return _unique_relation_items(definitions)


def _source_short_variable_tokens(source_text: str) -> list[str]:
    tokens: list[str] = []
    for match in re.finditer(r"(?:^|[；;\n\r。])\s*([A-Z][0-9]?)\s*[:：=]\s*([^；;\n\r。]{2,120})", source_text):
        desc = match.group(2).strip()
        if _looks_like_definition_description(desc):
            tokens.append(match.group(1).strip())
    return list(dict.fromkeys(tokens))


def _short_variable_has_semantic_context(section: str, variable: str) -> bool:
    for line in section.splitlines():
        if variable.lower() not in line.lower():
            continue
        text = re.sub(r"[`|*\-\s:：=]+", "", line)
        text = re.sub(re.escape(variable), "", text, flags=re.IGNORECASE)
        if len(text) < 6:
            return False
        semantic_terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]{2,}", text)
        generic_only = {"源文", "定义", "变量", "变更量", "含义", "待确认", "current", "source"}
        meaningful = [term for term in semantic_terms if term.lower() not in generic_only]
        if meaningful:
            return True
    return False


def _looks_like_definition_description(description: str) -> bool:
    if len(description.strip()) < 2:
        return False
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?\s*(?:[a-zA-Z%℃°/]+)?", description.strip()):
        return False
    return any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in description)


def _definition_terms(description: str) -> list[str]:
    terms: list[str] = []
    compact = re.sub(r"\s+", "", description)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,40}", description):
        if len(token) >= 2:
            terms.append(token)
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", compact)
    terms.extend(chunk for chunk in chinese_chunks if len(chunk) >= 2)
    if compact:
        terms.append(compact[:16])
    return list(dict.fromkeys(terms))


def _source_phase_relations(source_text: str) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    normalized = source_text.replace("\r\n", "\n")
    patterns = [
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}进入[\u4e00-\u9fffA-Za-z0-9_（）()]{1,24}后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}完成后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}结束后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}故障后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}恢复后)",
        r"([\u4e00-\u9fffA-Za-z0-9_（）()]{0,24}触发后)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            phrase = match.group(1).strip()
            terms = _phase_terms(phrase)
            if terms:
                phases.append({"phrase": phrase, "terms": terms})
    return _unique_relation_items(phases)


def _phase_terms(phrase: str) -> list[str]:
    compact = re.sub(r"\s+", "", phrase)
    terms = [compact]
    for suffix in ["进入", "完成后", "结束后", "故障后", "恢复后", "触发后"]:
        if suffix in compact:
            left = compact.split(suffix, 1)[0]
            right = suffix if suffix.endswith("后") else suffix + compact.split(suffix, 1)[1][:8]
            if left:
                terms.append(left[-12:])
            terms.append(right)
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))


def _normalize_for_relation_match(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _semantic_defined_terms(context: dict[str, Any]) -> list[dict[str, Any]]:
    model = context.get("source_semantic_model") if isinstance(context.get("source_semantic_model"), dict) else {}
    value = model.get("defined_terms") if isinstance(model, dict) else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _semantic_control_branches(context: dict[str, Any]) -> list[dict[str, Any]]:
    model = context.get("source_semantic_model") if isinstance(context.get("source_semantic_model"), dict) else {}
    value = model.get("control_branches") if isinstance(model, dict) else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _term_definition_line(section: str, symbol: str) -> str:
    symbol_norm = _normalize_for_relation_match(symbol)
    fallback = ""
    loose_fallback = ""
    for line in section.splitlines():
        if not line.strip():
            continue
        cells = _markdown_table_cells(line)
        if cells:
            first_cell = _normalize_for_relation_match(cells[0])
            if first_cell == symbol_norm:
                return line
            if not fallback and first_cell and first_cell != "符号" and (symbol_norm in first_cell or first_cell in symbol_norm):
                fallback = line
            continue
        line_norm = _normalize_for_relation_match(line)
        if not loose_fallback and symbol_norm in line_norm:
            loose_fallback = line
    return fallback or loose_fallback


def _markdown_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    cells = [cell.strip().strip("`") for cell in stripped.strip("|").split("|")]
    if not cells or all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
        return []
    return cells


def _has_semantic_field_coverage(expected: str, normalized_content: str) -> bool:
    if _has_meaningful_overlap(expected, normalized_content):
        return True
    terms = _semantic_field_terms(expected)
    if not terms:
        return True
    hits = sum(1 for term in terms if _normalize_for_relation_match(term) in normalized_content)
    if hits >= min(2, len(terms)):
        return True
    return hits >= 1 and len(terms) <= 2


def _term_line_has_structured_semantics(line: str) -> bool:
    cells = _markdown_table_cells(line)
    if len(cells) >= 7:
        semantic_cells = cells[1:7]
    else:
        semantic_cells = re.split(r"[；;]", line)
    meaningful_cells = 0
    generic = {"未知", "待确认", "源文定义", "变量", "符号", "无", ""}
    for cell in semantic_cells:
        normalized = _normalize_for_relation_match(cell).strip("|")
        if len(normalized) < 2 or normalized in generic:
            continue
        if normalized in {"系统", "对象", "当前"}:
            continue
        meaningful_cells += 1
    return meaningful_cells >= 4


def _term_line_has_calculation_cell(line: str) -> bool:
    cells = _markdown_table_cells(line)
    if len(cells) >= 7:
        candidate = cells[6]
    else:
        candidate = line
    normalized = _normalize_for_relation_match(candidate)
    if len(normalized) < 2:
        return False
    if normalized in {"未知", "待确认", "无"}:
        return False
    return any(token in normalized for token in ["差值", "计算", "判断", "控制", "比较", "线性", "持续", "维持", "阈值", "用于"]) or len(normalized) >= 4


def _term_line_symbol_extends_expected(line: str, symbol: str) -> bool:
    cells = _markdown_table_cells(line)
    if not cells:
        return False
    symbol_norm = _normalize_for_relation_match(symbol)
    first_cell = _normalize_for_relation_match(cells[0])
    return first_cell != symbol_norm and symbol_norm in first_cell


def _semantic_field_terms(text: str) -> list[str]:
    compact = _normalize_for_relation_match(text)
    raw_terms = _definition_terms(text)
    terms: list[str] = []
    stopwords = {
        "用于",
        "作为",
        "根据",
        "进行",
        "控制",
        "系统",
        "信号",
        "状态",
        "当前",
        "实时",
        "时刻",
        "持续",
        "比较",
        "判断",
        "条件",
        "范围",
        "调整",
        "负责",
    }
    for term in raw_terms:
        normalized = _normalize_for_relation_match(term)
        if len(normalized) < 2 or normalized in stopwords:
            continue
        if re.fullmatch(r"[a-z]{1,2}", normalized):
            continue
        terms.append(term)
    terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9_]{1,40}", text))
    for token in ["差值", "线性", "阈值", "水温", "温度", "初始", "实时", "高压", "保持", "继电器", "控制器"]:
        if token in compact:
            terms.append(token)
    return list(dict.fromkeys(terms))


def _has_meaningful_overlap(expected: str, normalized_content: str) -> bool:
    terms = _definition_terms(expected)
    meaningful = [term for term in terms if len(term) >= 2]
    if not meaningful:
        return True
    return any(_normalize_for_relation_match(term) in normalized_content for term in meaningful)


def _has_controlled_object_overlap(expected: str, normalized_content: str) -> bool:
    if _has_meaningful_overlap(expected, normalized_content):
        return True
    compact = _normalize_for_relation_match(expected)
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]{3,}", compact)
    aliases: list[str] = []
    for chunk in chinese_chunks:
        for size in range(2, min(4, len(chunk)) + 1):
            aliases.append(chunk[-size:])
            aliases.append(chunk[:size])
    return any(alias in normalized_content for alias in aliases)


def _unique_relation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("name") or item.get("phrase") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
