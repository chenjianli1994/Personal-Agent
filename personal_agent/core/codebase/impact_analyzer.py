from __future__ import annotations

from pathlib import Path
from typing import Any

from .retriever import (
    call_graph_query,
    codebase_search,
    include_impact_query,
    macro_impact_query,
    symbol_lookup,
    type_usage_query,
    variable_usage_query,
)


def analyze_codebase_impact(db_path: Path, project_id: int, change_hint: str, *, limit: int = 8) -> dict[str, Any]:
    search = codebase_search(db_path, project_id, change_hint, limit=limit)
    candidate_files = search.get("file_results", []) if search.get("passed") else []
    candidate_symbols = search.get("symbol_results", []) if search.get("passed") else []
    candidate_functions = [item for item in candidate_symbols if item.get("kind") == "function"][:limit]
    candidate_types = [item for item in candidate_symbols if item.get("kind") in {"typedef", "struct", "enum"}][:limit]
    candidate_macros = [item for item in candidate_symbols if item.get("kind") == "macro"][:limit]
    candidate_variables = [item for item in candidate_symbols if item.get("kind") == "variable"][:limit]

    call_impacts = [call_graph_query(db_path, project_id, item["name"], limit=limit) for item in candidate_functions]
    type_impacts = [type_usage_query(db_path, project_id, item["name"], limit=limit) for item in candidate_types]
    macro_impacts = [macro_impact_query(db_path, project_id, item["name"], limit=limit) for item in candidate_macros]
    variable_impacts = [variable_usage_query(db_path, project_id, item["name"], limit=limit) for item in candidate_variables]

    include_impacts = []
    for item in candidate_files:
        path = str(item.get("path", ""))
        if path.endswith((".h", ".hpp", ".hh")):
            include_impacts.append(include_impact_query(db_path, project_id, path))

    explicit_symbol = _first_exact_symbol(db_path, project_id, change_hint)
    if explicit_symbol and explicit_symbol.get("kind") == "function" and explicit_symbol["name"] not in {item.get("name") for item in candidate_functions}:
        candidate_functions.append(explicit_symbol)
        call_impacts.append(call_graph_query(db_path, project_id, explicit_symbol["name"], limit=limit))

    impacted_tests = [
        item
        for item in candidate_files
        if "test" in str(item.get("path", "")).lower() or item.get("kind") == "test"
    ][:limit]
    affected_files = _unique(
        [
            *[str(item.get("path", "")) for item in candidate_files],
            *[path for impact in include_impacts for path in impact.get("affected_files", [])],
            *[edge.get("file", "") for impact in call_impacts for edge in impact.get("callers", [])],
            *[edge.get("file", "") for impact in call_impacts for edge in impact.get("callees", [])],
            *[path for impact in type_impacts for path in impact.get("referencing_files", [])],
            *[path for impact in macro_impacts for path in impact.get("affected_files", [])],
            *[ref.get("file", "") for impact in variable_impacts for ref in impact.get("references", [])],
        ]
    )
    candidate_modules = _candidate_modules(candidate_files)
    evidence_confidence = _evidence_confidence(
        candidate_files,
        candidate_symbols,
        call_impacts,
        include_impacts,
        type_impacts,
        macro_impacts,
        variable_impacts,
    )
    limitations = []
    if not search.get("passed"):
        limitations.extend(search.get("limitations", []))
    if not candidate_files:
        limitations.append("no candidate files found for change hint")
    if not call_impacts:
        limitations.append("no function call impact was available for the matched hint")
    if not include_impacts:
        limitations.append("no header include impact was available for the matched hint")
    unresolved = [
        "function pointer calls are not resolved",
        "macro expansion values are not evaluated",
        "conditional compilation truth values are not evaluated",
        "variable read/write references are regex-based and may miss aliasing",
    ]
    return {
        "passed": bool(search.get("passed")),
        "analysis_stage": 2,
        "change_hint": change_hint,
        "candidate_modules": candidate_modules,
        "candidate_files": candidate_files,
        "candidate_symbols": candidate_symbols,
        "candidate_functions": candidate_functions,
        "candidate_types": candidate_types,
        "candidate_macros": candidate_macros,
        "candidate_variables": candidate_variables,
        "affected_files": affected_files,
        "impacted_tests": impacted_tests,
        "call_impacts": call_impacts,
        "include_impacts": include_impacts,
        "type_impacts": type_impacts,
        "macro_impacts": macro_impacts,
        "variable_impacts": variable_impacts,
        "evidence_confidence": evidence_confidence,
        "verification_failure_attribution": _verification_failure_attribution(change_hint, candidate_files, candidate_functions, impacted_tests),
        "impact_summary": {
            "changed_functions_affect_callers": _unique([edge.get("caller", "") for impact in call_impacts for edge in impact.get("callers", [])]),
            "changed_headers_affect_units": _unique([path for impact in include_impacts for path in impact.get("affected_files", [])]),
            "changed_types_affect_interfaces": _unique([item.get("name", "") for impact in type_impacts for item in impact.get("interfaces", [])]),
            "changed_macros_affect_branches": _unique([block.get("variant_key", "") for impact in macro_impacts for block in impact.get("conditional_blocks", [])]),
            "changed_variables_affect_functions": _unique([ref.get("function", "") for impact in variable_impacts for ref in impact.get("references", [])]),
        },
        "risk_notes": [
            "Stage 2 impact analysis is still read-only and heuristic.",
            "Call graph, include graph, macro conditions, type usage, and variable references are indexed as explicit evidence with known uncertainty.",
        ],
        "unresolved_items": unresolved,
        "limitations": _unique(limitations),
        "evidence_refs": {
            "code_files": affected_files,
            "code_symbols": _unique(
                [
                    *[item.get("name", "") for item in candidate_symbols],
                    *[edge.get("caller", "") for impact in call_impacts for edge in impact.get("callers", [])],
                    *[edge.get("callee", "") for impact in call_impacts for edge in impact.get("callees", [])],
                    *[item.get("name", "") for impact in type_impacts for item in impact.get("interfaces", [])],
                    *[ref.get("function", "") for impact in variable_impacts for ref in impact.get("references", [])],
                ]
            ),
        },
    }


def _first_exact_symbol(db_path: Path, project_id: int, text: str) -> dict[str, Any]:
    for token in _identifier_tokens(text):
        lookup = symbol_lookup(db_path, project_id, token, limit=1)
        matches = lookup.get("matches", []) if lookup.get("passed") else []
        if matches and matches[0].get("name") == token:
            return matches[0]
    return {}


def _identifier_tokens(text: str) -> list[str]:
    import re

    return re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text)


def _candidate_modules(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    for item in files:
        path = str(item.get("path", ""))
        module = path.split("/", 1)[0] if "/" in path else Path(path).stem
        entry = modules.setdefault(module, {"module": module, "files": [], "max_relevance": 0.0})
        entry["files"].append(path)
        entry["max_relevance"] = max(float(entry["max_relevance"]), float(item.get("relevance", 0)))
    return sorted(modules.values(), key=lambda item: item["max_relevance"], reverse=True)


def _evidence_confidence(
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    call_impacts: list[dict[str, Any]],
    include_impacts: list[dict[str, Any]],
    type_impacts: list[dict[str, Any]],
    macro_impacts: list[dict[str, Any]],
    variable_impacts: list[dict[str, Any]],
) -> float:
    score = 0.2
    if files:
        score += 0.2
    if symbols:
        score += 0.15
    if any(impact.get("callers") or impact.get("callees") for impact in call_impacts):
        score += 0.15
    if any(impact.get("affected_files") for impact in include_impacts):
        score += 0.1
    if any(impact.get("interfaces") or impact.get("referencing_files") for impact in type_impacts):
        score += 0.08
    if any(impact.get("conditional_blocks") for impact in macro_impacts):
        score += 0.06
    if any(impact.get("references") for impact in variable_impacts):
        score += 0.06
    return round(min(score, 0.9), 3)


def _verification_failure_attribution(
    change_hint: str,
    files: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    tests: list[dict[str, Any]],
) -> dict[str, Any]:
    lowered = change_hint.lower()
    code_hits = [item.get("path", "") for item in files if item.get("path") and "test" not in str(item.get("path", "")).lower()]
    test_hits = [item.get("path", "") for item in tests if item.get("path")]
    if "fail" in lowered or "失败" in lowered or "boundary" in lowered or "边界" in lowered:
        likely_source = "code_logic" if code_hits else ("test_expectation" if test_hits else "unknown")
    else:
        likely_source = "unknown"
    confidence = 0.65 if likely_source != "unknown" and functions else (0.45 if likely_source != "unknown" else 0.25)
    return {
        "likely_source": likely_source,
        "confidence": round(confidence, 3),
        "candidate_code_files": code_hits[:8],
        "candidate_test_files": test_hits[:8],
        "candidate_functions": [item.get("name", "") for item in functions[:8]],
        "rationale": "failure attribution is inferred from indexed code/test evidence and task wording",
    }


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
