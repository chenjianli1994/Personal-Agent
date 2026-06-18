from __future__ import annotations


def looks_like_document_generation(text: str) -> bool:
    compact = _compact(text)
    return any(token in compact for token in ["生成", "写一份", "输出", "创建"]) and any(
        token in compact for token in ["报告", "文档", "需求分析", "需求拆解", "功能规范", "详细设计", "测试用例", "单元测试代码", "测试代码"]
    )


def document_type_from_text(text: str) -> str:
    compact = _compact(text)
    if "需求拆解" in compact:
        return "requirement_breakdown"
    if "功能规范" in compact:
        return "functional_spec"
    if "详细设计" in compact:
        return "detailed_design"
    if "测试用例" in compact:
        return "test_case_spec"
    if "单元测试代码" in compact or "测试代码" in compact:
        return "unit_test_code_or_diff"
    return "requirement_analysis_report"


def _compact(text: str) -> str:
    return "".join(str(text or "").split()).lower()
