from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .database import connect
from .utils import json_dumps, utc_now


CODE_STYLE_QUERY = "C SWE.3 SWE.4 code style coding standard naming indentation unit test"


def build_code_style_profile(db_path: Path, project_id: int) -> dict[str, Any]:
    samples = _load_style_samples(db_path, project_id)
    profile = _derive_profile(samples)
    now = utc_now()
    source_refs = [
        {"id": item["id"], "title": item["title"], "category": item["category"], "source_ref": item["source_ref"]}
        for item in samples[:12]
    ]
    confidence = min(0.95, 0.25 + len(samples) * 0.08 + len(profile["rules"]) * 0.04) if samples else 0.0
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO code_style_profiles(project_id, profile_json, source_refs_json, sample_count, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                profile_json=excluded.profile_json,
                source_refs_json=excluded.source_refs_json,
                sample_count=excluded.sample_count,
                confidence=excluded.confidence,
                status='active',
                updated_at=excluded.updated_at
            """,
            (project_id, json_dumps(profile), json_dumps(source_refs), len(samples), confidence, now, now),
        )
        _audit(conn, project_id, "CODE_STYLE_PROFILE_BUILT", f"从知识库抽取代码规范 Profile：{len(samples)} 个样例", {"sample_count": len(samples), "confidence": confidence})
        row = conn.execute("SELECT * FROM code_style_profiles WHERE project_id=?", (project_id,)).fetchone()
    return _decode_profile(dict(row))


def get_code_style_profile(db_path: Path, project_id: int) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM code_style_profiles WHERE project_id=?", (project_id,)).fetchone()
    if not row:
        return {
            "project_id": project_id,
            "profile": _empty_profile(),
            "source_refs": [],
            "sample_count": 0,
            "confidence": 0.0,
            "status": "missing",
            "created_at": "",
            "updated_at": "",
        }
    return _decode_profile(dict(row))


def ensure_code_style_profile(db_path: Path, project_id: int) -> dict[str, Any]:
    current = get_code_style_profile(db_path, project_id)
    if current.get("sample_count", 0) > 0 and current.get("status") == "active":
        return current
    return build_code_style_profile(db_path, project_id)


def evaluate_patch_style(profile_row: dict[str, Any], patch_text: str) -> dict[str, Any]:
    profile = profile_row.get("profile") or _empty_profile()
    added_lines = [
        line[1:]
        for line in patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
    ]
    findings: list[str] = []
    if not profile_row.get("sample_count"):
        return {
            "passed": True,
            "summary": "未找到可抽取的代码风格资料，本次风格检查按非阻塞处理。",
            "findings": ["知识库缺少可用代码样例或编码规范，建议导入历史 .c/.h/测试文件后重建 Style Profile。"],
            "checked_lines": len(added_lines),
        }

    indent = profile.get("indentation", {})
    expected_indent = int(indent.get("spaces", 0) or 0)
    if expected_indent:
        mismatches = [
            line
            for line in added_lines
            if line.startswith(" ") and len(line) - len(line.lstrip(" ")) not in {expected_indent, expected_indent * 2, expected_indent * 3}
        ]
        if len(mismatches) > max(3, len(added_lines) // 4):
            findings.append(f"新增代码缩进与资料库主风格不一致，期望 {expected_indent} 空格缩进。")

    naming = profile.get("naming", {})
    expected_function_case = naming.get("function_case", "")
    if expected_function_case:
        functions = _function_names("\n".join(added_lines))
        bad_names = [name for name in functions if _name_case(name) != expected_function_case]
        if bad_names:
            findings.append(f"函数命名不符合 {expected_function_case}: {', '.join(bad_names[:5])}")

    if any("TODO" in line or "stub" in line.lower() for line in added_lines):
        findings.append("新增代码包含 TODO/stub，不能作为已理解规范后的正式实现。")

    passed = not findings
    return {
        "passed": passed,
        "summary": "代码风格与资料库 Profile 一致" if passed else "代码风格与资料库 Profile 存在偏差",
        "findings": findings or ["未发现新增代码风格偏差。"],
        "checked_lines": len(added_lines),
    }


def _load_style_samples(db_path: Path, project_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, category, source_ref, content, tags_json, updated_at
            FROM knowledge_items
            WHERE status='active' AND (project_id=? OR project_id IS NULL)
            ORDER BY updated_at DESC
            """,
            (project_id,),
        ).fetchall()
    samples = []
    for row in rows:
        payload = dict(row)
        tags = _loads(payload.pop("tags_json", "[]"))
        haystack = " ".join([payload["title"], payload["category"], payload["source_ref"], " ".join(tags)]).lower()
        content = str(payload.get("content", ""))
        is_code_file = str(payload["source_ref"]).lower().endswith((".c", ".h", ".cpp", ".hpp", ".cc", ".hh"))
        has_code_signal = any(token in content for token in ["#include", "return ", "void ", "int ", "ASSERT", "assert("])
        is_style_doc = any(token in haystack for token in ["code_style", "coding", "代码", "规范"])
        if payload["category"] in {"c_code_template", "quality_rule"} or is_code_file or is_style_doc or (payload["category"] == "test_template" and has_code_signal):
            samples.append(payload)
    return samples


def _derive_profile(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return _empty_profile()
    content = "\n".join(str(item.get("content", "")) for item in samples)
    indent_widths = Counter()
    for line in content.splitlines():
        if line.startswith(" ") and line.strip():
            count = len(line) - len(line.lstrip(" "))
            if count <= 12:
                indent_widths[count] += 1
    function_cases = Counter(_name_case(name) for name in _function_names(content))
    include_guard_cases = Counter(_include_guard_patterns(content))
    comment_styles = Counter(_comment_styles(content))
    rules = []
    if indent_widths:
        width = _first_common_indent(indent_widths)
        rules.append(f"缩进优先使用 {width} 个空格。")
    else:
        width = 0
    if function_cases:
        case = function_cases.most_common(1)[0][0]
        rules.append(f"函数命名优先使用 {case}。")
    else:
        case = ""
    if include_guard_cases:
        rules.append(f"头文件保护宏风格：{include_guard_cases.most_common(1)[0][0]}。")
    if comment_styles:
        rules.append(f"注释风格优先：{comment_styles.most_common(1)[0][0]}。")
    if "ASSERT" in content or "assert" in content:
        rules.append("测试代码沿用资料库中的断言/Mock 写法。")
    return {
        "language": "C/C++" if any(str(item.get("source_ref", "")).lower().endswith((".c", ".h", ".cpp", ".hpp")) for item in samples) else "mixed",
        "rules": rules,
        "indentation": {"spaces": width, "evidence_count": sum(indent_widths.values())},
        "naming": {"function_case": case, "observed": dict(function_cases)},
        "include_guards": dict(include_guard_cases),
        "comment_styles": dict(comment_styles),
        "generation_contract": [
            "生成代码前必须优先读取本 Profile 和来源样例。",
            "新增接口、函数、测试命名应符合 Profile 中的主风格。",
            "无法判断时必须在 Agent 计划中声明资料缺口，不能假装已匹配。",
        ],
    }


def _function_names(content: str) -> list[str]:
    pattern = re.compile(r"^[A-Za-z_][\w\s\*]*\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{", re.MULTILINE)
    keywords = {"if", "for", "while", "switch"}
    return [name for name in pattern.findall(content) if name not in keywords]


def _name_case(name: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return "snake_case"
    if re.fullmatch(r"[a-z][A-Za-z0-9]*", name) and any(char.isupper() for char in name):
        return "camelCase"
    if re.fullmatch(r"[A-Z][A-Za-z0-9]*", name):
        return "PascalCase"
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        return "UPPER_SNAKE_CASE"
    return "mixed"


def _first_common_indent(widths: Counter[int]) -> int:
    for candidate in [2, 4, 8]:
        if widths[candidate]:
            return candidate
    return widths.most_common(1)[0][0]


def _include_guard_patterns(content: str) -> list[str]:
    guards = re.findall(r"#ifndef\s+([A-Za-z_][A-Za-z0-9_]*)", content)
    return [_name_case(item) for item in guards]


def _comment_styles(content: str) -> list[str]:
    styles = []
    if "//" in content:
        styles.append("line_comment")
    if "/*" in content and "*/" in content:
        styles.append("block_comment")
    return styles


def _empty_profile() -> dict[str, Any]:
    return {
        "language": "",
        "rules": [],
        "indentation": {},
        "naming": {},
        "include_guards": {},
        "comment_styles": {},
        "generation_contract": ["资料库尚未形成代码规范，生成前需要导入历史代码或编码规范。"],
    }


def _decode_profile(row: dict[str, Any]) -> dict[str, Any]:
    row["profile"] = _loads(row.pop("profile_json", "{}"))
    row["source_refs"] = _loads(row.pop("source_refs_json", "[]"))
    row["confidence"] = float(row.get("confidence", 0) or 0)
    return row


def _audit(conn, project_id: int, event_type: str, message: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_events(project_id, event_type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, event_type, message, json_dumps(payload), utc_now()),
    )


def _loads(text: str) -> Any:
    try:
        return json.loads(text or "{}")
    except Exception:
        return {}
