from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from .file_text import CODE_FILE_RELEVANCE_CHARS, read_code_text
from .index_store import (
    build_and_store_index,
    latest_repository,
    repository_call_edges,
    repository_conditional_blocks,
    repository_files,
    repository_includes,
    repository_symbols,
    repository_variable_references,
    resolve_repo_path,
)


def build_codebase_index(
    db_path: Path,
    project_id: int,
    *,
    repo_path: str = "",
    query: str = "",
    max_files: int = 160,
    skip_dirs: list[str] | None = None,
    batch_size: int = 0,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    index = build_and_store_index(
        db_path,
        project_id,
        repo_path=repo_path,
        max_files=max_files,
        skip_dirs=skip_dirs,
        batch_size=batch_size,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )
    if not index.get("exists"):
        return _missing_index(project_id, index.get("limitations", ["code repository is unavailable"])[0], index.get("code_repo_path", ""))
    repo_id = int(index["repository_id"])
    files = repository_files(db_path, repo_id)
    symbols = repository_symbols(db_path, repo_id)
    includes = repository_includes(db_path, repo_id)
    call_edges = repository_call_edges(db_path, repo_id)
    conditionals = repository_conditional_blocks(db_path, repo_id)
    variable_refs = repository_variable_references(db_path, repo_id)
    query_terms = _terms(query)
    relevant_files = _relevant_files(db_path, repo_id, files, query_terms)
    relevant_symbols = _relevant_symbols(symbols, relevant_files, query_terms)
    tests = [{"path": item["path"], "reason": "name_or_content_has_test_signal"} for item in files if item["file_type"] == "test"]
    impacted_tests = _impacted_tests(tests, relevant_files)
    component_map = _component_map(files)
    confidence = _confidence(files, symbols, query_terms)
    limitations = list(index.get("limitations", []))
    if not query_terms:
        limitations.append("no task-specific query terms were available; relevance is generic")
    return {
        "project_id": project_id,
        "repository_id": repo_id,
        "code_repo_path": index["code_repo_path"],
        "exists": True,
        "file_count": len(files),
        "symbol_count": len(symbols),
        "dependency_count": len(includes),
        "include_count": len(includes),
        "call_edge_count": len(call_edges),
        "conditional_block_count": len(conditionals),
        "variable_reference_count": len(variable_refs),
        "test_file_count": len(tests),
        "skipped_after_limit": index.get("skipped_after_limit", 0),
        "skipped_by_dir": index.get("skipped_by_dir", 0),
        "component_map": component_map,
        "relevant_files": relevant_files[:12],
        "relevant_symbols": relevant_symbols[:16],
        "dependencies": [_compact_include(item) for item in includes[:80]],
        "impacted_tests": impacted_tests[:8],
        "contracts": _contracts(symbols, includes, tests),
        "confidence": confidence,
        "parser_confidence": index.get("parser_confidence", 0.0),
        "limitations": _unique(limitations),
        "parser": index.get("parser", "regex"),
        "index_run": {
            "id": index.get("index_run_id"),
            "changed_file_count": index.get("changed_file_count", 0),
            "reused_file_count": index.get("reused_file_count", 0),
            "skipped_unchanged_count": index.get("skipped_unchanged_count", 0),
            "deleted_file_count": index.get("deleted_file_count", 0),
            "skipped_after_limit": index.get("skipped_after_limit", 0),
            "skipped_by_dir": index.get("skipped_by_dir", 0),
            "batch_size": index.get("batch_size", 0),
            "batch_count": index.get("batch_count", 0),
            "parser": index.get("parser", "regex"),
            "parser_confidence": index.get("parser_confidence", 0.0),
            "metadata": index.get("index_run", {}),
        },
        "analysis_indexes": {
            "call_graph": len(call_edges),
            "macro_index": len(conditionals),
            "type_index": sum(1 for item in symbols if item.get("kind") in {"typedef", "struct", "enum"}),
            "variable_index": len(variable_refs),
        },
        "index_run_id": index.get("index_run_id"),
        "evidence_refs": {
            "code_files": [item["path"] for item in relevant_files[:12]],
            "code_symbols": [item["name"] for item in relevant_symbols[:16]],
        },
    }


def compact_codebase_index(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "exists": bool(index.get("exists")),
        "code_repo_path": index.get("code_repo_path", ""),
        "file_count": index.get("file_count", 0),
        "symbol_count": index.get("symbol_count", 0),
        "dependency_count": index.get("dependency_count", 0),
        "test_file_count": index.get("test_file_count", 0),
        "relevant_files": index.get("relevant_files", [])[:8],
        "relevant_symbols": index.get("relevant_symbols", [])[:10],
        "impacted_tests": index.get("impacted_tests", [])[:8],
        "contracts": index.get("contracts", {}),
        "confidence": index.get("confidence", 0),
        "parser_confidence": index.get("parser_confidence", 0),
        "limitations": index.get("limitations", [])[:6],
        "parser": index.get("parser", "regex"),
        "index_run": index.get("index_run", {}),
        "analysis_indexes": index.get("analysis_indexes", {}),
    }


def codebase_search(db_path: Path, project_id: int, query: str, *, limit: int = 8) -> dict[str, Any]:
    repo = _ensure_index(db_path, project_id)
    if not repo:
        return {"passed": False, "query": query, "results": [], "limitations": ["code repository is not indexed and code_repo_path is not configured"], "evidence_refs": {}}
    repo_id = int(repo["id"])
    files = repository_files(db_path, repo_id)
    symbols = repository_symbols(db_path, repo_id)
    terms = _terms(query)
    file_hits = _relevant_files(db_path, repo_id, files, terms, limit=limit)
    symbol_hits = _relevant_symbols(symbols, file_hits, terms)[:limit]
    return {
        "passed": True,
        "query": query,
        "repository_id": repo_id,
        "file_results": file_hits,
        "symbol_results": symbol_hits,
        "result_count": len(file_hits) + len(symbol_hits),
        "limitations": [] if terms else ["query has no searchable terms"],
        "evidence_refs": {"code_files": [item["path"] for item in file_hits], "code_symbols": [item["name"] for item in symbol_hits]},
    }


def symbol_lookup(db_path: Path, project_id: int, name: str, *, kind: str = "", limit: int = 20) -> dict[str, Any]:
    repo = _ensure_index(db_path, project_id)
    if not repo:
        return {"passed": False, "name": name, "matches": [], "limitations": ["code repository is not indexed and code_repo_path is not configured"], "evidence_refs": {}}
    symbols = repository_symbols(db_path, int(repo["id"]))
    needle = name.lower()
    matches = []
    for symbol in symbols:
        if kind and symbol["kind"] != kind:
            continue
        score = 2 if str(symbol["name"]).lower() == needle else (1 if needle in str(symbol["name"]).lower() else 0)
        if symbol.get("metadata", {}).get("definition"):
            score += 1
        if score:
            matches.append((score, symbol))
    ranked = [item for _, item in sorted(matches, key=lambda pair: (pair[0], -int(pair[1].get("start_line", 0))), reverse=True)[:limit]]
    return {
        "passed": True,
        "name": name,
        "kind": kind,
        "match_count": len(ranked),
        "matches": [_compact_symbol(item) for item in ranked],
        "limitations": [] if ranked else ["symbol was not found in the indexed repository"],
        "evidence_refs": {"code_symbols": [item["name"] for item in ranked], "code_files": [item["file_path"] for item in ranked]},
    }


def include_impact_query(db_path: Path, project_id: int, path: str) -> dict[str, Any]:
    repo = _ensure_index(db_path, project_id)
    if not repo:
        return {"passed": False, "path": path, "affected_files": [], "limitations": ["code repository is not indexed and code_repo_path is not configured"], "evidence_refs": {}}
    repo_id = int(repo["id"])
    includes = repository_includes(db_path, repo_id)
    normalized = path.replace("\\", "/").strip()
    direct = [item for item in includes if item.get("resolved_path") == normalized or item.get("include_text") == Path(normalized).name]
    direct_affected = sorted({item["source_path"] for item in direct})
    transitive_affected = _transitive_include_sources(includes, normalized)
    affected = _unique([*direct_affected, *transitive_affected])
    return {
        "passed": True,
        "path": normalized,
        "affected_files": affected,
        "direct_affected_files": direct_affected,
        "transitive_affected_files": [item for item in transitive_affected if item not in direct_affected],
        "direct_include_edges": [_compact_include(item) for item in direct],
        "limitations": [] if affected else ["no include edges found; unresolved includes may hide impact"],
        "evidence_refs": {"code_files": affected},
    }


def call_graph_query(db_path: Path, project_id: int, function_name: str, *, limit: int = 20) -> dict[str, Any]:
    repo = _ensure_index(db_path, project_id)
    if not repo:
        return {"passed": False, "function_name": function_name, "callers": [], "callees": [], "limitations": ["code repository is not indexed and code_repo_path is not configured"], "evidence_refs": {}}
    repo_id = int(repo["id"])
    edges = repository_call_edges(db_path, repo_id)
    callers = [_compact_call_edge(item, direction="caller") for item in edges if item["callee_name"] == function_name][:limit]
    callees = [_compact_call_edge(item, direction="callee") for item in edges if item["caller_name"] == function_name][:limit]
    return {
        "passed": True,
        "function_name": function_name,
        "callers": callers,
        "callees": callees,
        "caller_count": len(callers),
        "callee_count": len(callees),
        "limitations": [] if callers or callees else ["no direct call graph edges found for function"],
        "evidence_refs": {
            "code_symbols": _unique([function_name, *[item["caller"] for item in callers], *[item["callee"] for item in callees]]),
            "code_files": _unique([*[item["file"] for item in callers], *[item["file"] for item in callees]]),
        },
    }


def macro_impact_query(db_path: Path, project_id: int, macro_name: str, *, limit: int = 20) -> dict[str, Any]:
    repo = _ensure_index(db_path, project_id)
    if not repo:
        return {"passed": False, "macro_name": macro_name, "conditional_blocks": [], "limitations": ["code repository is not indexed and code_repo_path is not configured"], "evidence_refs": {}}
    repo_id = int(repo["id"])
    blocks = repository_conditional_blocks(db_path, repo_id)
    matched = [item for item in blocks if macro_name in item.get("macros", []) or macro_name in str(item.get("expression", ""))][:limit]
    compact = [_compact_conditional(item) for item in matched]
    return {
        "passed": True,
        "macro_name": macro_name,
        "conditional_blocks": compact,
        "affected_files": _unique([item["file"] for item in compact]),
        "limitations": [] if compact else ["macro was not found in indexed conditional compilation blocks"],
        "evidence_refs": {"code_files": _unique([item["file"] for item in compact]), "code_symbols": [macro_name]},
    }


def variable_usage_query(db_path: Path, project_id: int, variable_name: str, *, limit: int = 20) -> dict[str, Any]:
    repo = _ensure_index(db_path, project_id)
    if not repo:
        return {"passed": False, "variable_name": variable_name, "references": [], "limitations": ["code repository is not indexed and code_repo_path is not configured"], "evidence_refs": {}}
    repo_id = int(repo["id"])
    refs = repository_variable_references(db_path, repo_id)
    matched = [item for item in refs if item["variable_name"] == variable_name][:limit]
    compact = [_compact_variable_reference(item) for item in matched]
    return {
        "passed": True,
        "variable_name": variable_name,
        "references": compact,
        "readers": [item for item in compact if item["access_type"] in {"read", "address"}],
        "writers": [item for item in compact if item["access_type"] == "write"],
        "limitations": [] if compact else ["variable was not found in indexed read/write references"],
        "evidence_refs": {"code_files": _unique([item["file"] for item in compact]), "code_symbols": [variable_name]},
    }


def type_usage_query(db_path: Path, project_id: int, type_name: str, *, limit: int = 20) -> dict[str, Any]:
    repo = _ensure_index(db_path, project_id)
    if not repo:
        return {"passed": False, "type_name": type_name, "interfaces": [], "limitations": ["code repository is not indexed and code_repo_path is not configured"], "evidence_refs": {}}
    repo_id = int(repo["id"])
    symbols = repository_symbols(db_path, repo_id)
    files = repository_files(db_path, repo_id)
    type_symbols = [item for item in symbols if item["name"] == type_name and item["kind"] in {"typedef", "struct", "enum"}]
    interfaces = [
        _compact_symbol(item)
        for item in symbols
        if type_name in str(item.get("signature", "")) and item["kind"] in {"function", "typedef", "struct", "enum", "variable"}
    ][:limit]
    file_refs = _files_containing_type(db_path, repo_id, files, type_name, limit)
    return {
        "passed": True,
        "type_name": type_name,
        "definitions": [_compact_symbol(item) for item in type_symbols],
        "interfaces": interfaces,
        "referencing_files": file_refs,
        "limitations": [] if type_symbols or interfaces or file_refs else ["type was not found in indexed symbols or text references"],
        "evidence_refs": {"code_files": _unique([*[item["file_path"] for item in interfaces], *file_refs]), "code_symbols": _unique([type_name, *[item["name"] for item in interfaces]])},
    }


def _ensure_index(db_path: Path, project_id: int) -> dict[str, Any] | None:
    repo = latest_repository(db_path, project_id)
    if repo:
        return repo
    configured = resolve_repo_path(db_path, project_id, "")
    if not configured:
        return None
    built = build_codebase_index(db_path, project_id)
    if not built.get("exists"):
        return None
    return latest_repository(db_path, project_id)


def _missing_index(project_id: int, reason: str, repo_path: str = "") -> dict[str, Any]:
    return {
        "project_id": project_id,
        "code_repo_path": repo_path,
        "exists": False,
        "file_count": 0,
        "symbol_count": 0,
        "dependency_count": 0,
        "include_count": 0,
        "test_file_count": 0,
        "component_map": {},
        "relevant_files": [],
        "relevant_symbols": [],
        "dependencies": [],
        "impacted_tests": [],
        "contracts": {},
        "confidence": 0.0,
        "limitations": [reason],
        "parser": "regex",
        "parser_confidence": 0.0,
        "index_run": {},
    }


def _transitive_include_sources(includes: list[dict[str, Any]], target: str) -> list[str]:
    current = {target}
    affected: list[str] = []
    seen_edges: set[tuple[str, str]] = set()
    while current:
        next_targets: set[str] = set()
        for item in includes:
            source = str(item.get("source_path", ""))
            destination = str(item.get("resolved_path") or item.get("include_text") or "")
            edge = (source, destination)
            if edge in seen_edges:
                continue
            if destination in current or Path(destination).name in {Path(value).name for value in current}:
                seen_edges.add(edge)
                if source not in affected:
                    affected.append(source)
                    next_targets.add(source)
        current = next_targets
    return affected


def _relevant_files(db_path: Path, repository_id: int, files: list[dict[str, Any]], query_terms: set[str], *, limit: int = 12) -> list[dict[str, Any]]:
    from ..database import connect

    with connect(db_path) as conn:
        row = conn.execute("SELECT root_path FROM code_repositories WHERE id=?", (repository_id,)).fetchone()
        file_ids = [int(item["id"]) for item in files if item.get("id") is not None]
        symbol_counts = _count_by_file(
            conn,
            "code_symbols",
            "file_id",
            repository_id=repository_id,
            file_ids=file_ids,
        )
        include_counts = _count_by_file(
            conn,
            "code_includes",
            "source_file_id",
            repository_id=repository_id,
            file_ids=file_ids,
        )
    repo_root = str(row["root_path"]) if row else ""
    ranked = []
    for item in files:
        text = _indexed_source_text(repo_root, item)
        relevance = _score_relevance(item["path"], text, query_terms)
        file_id = int(item["id"])
        ranked.append(
            (
                relevance,
                int(item.get("line_count", 0)),
                {
                    "path": item["path"],
                    "kind": item["file_type"],
                    "language": item["language"],
                    "line_count": item["line_count"],
                    "symbol_count": symbol_counts.get(file_id, 0),
                    "dependency_count": include_counts.get(file_id, 0),
                    "relevance": relevance,
                    "snippet": _snippet_for_terms(text, query_terms),
                },
            )
        )
    return [item for _, _, item in sorted(ranked, key=lambda pair: (pair[0], pair[1]), reverse=True)[:limit]]


def _relevant_symbols(symbols: list[dict[str, Any]], relevant_files: list[dict[str, Any]], query_terms: set[str]) -> list[dict[str, Any]]:
    files = {item["path"] for item in relevant_files[:8]}
    ranked = []
    for symbol in symbols:
        score = 1 if symbol["file_path"] in files else 0
        lname = str(symbol["name"]).lower()
        score += sum(1 for term in query_terms if term.lower() in lname)
        if score > 0:
            ranked.append((score, _compact_symbol(symbol)))
    return [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)]


def _impacted_tests(tests: list[dict[str, str]], relevant_files: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not tests:
        return []
    stems = {Path(str(item["path"])).stem.lower().replace("test_", "").replace("_test", "") for item in relevant_files[:8]}
    impacted = []
    for test in tests:
        name = Path(test["path"]).stem.lower()
        if any(stem and stem in name for stem in stems):
            impacted.append({**test, "impact_reason": "matches_relevant_file_stem"})
    return impacted[:8] or tests[:5]


def _contracts(symbols: list[dict[str, Any]], includes: list[dict[str, Any]], tests: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "must_preserve_public_interfaces": [
            _compact_symbol(item)
            for item in symbols
            if item["kind"] in {"function", "typedef", "struct", "enum"} and str(item["file_path"]).endswith((".h", ".hpp", ".hh"))
        ][:12],
        "must_update_related_tests": [item["path"] for item in tests[:12]],
        "must_check_dependencies": [_compact_include(item) for item in includes[:12]],
    }


def _component_map(files: list[dict[str, Any]]) -> dict[str, int]:
    components: dict[str, int] = {}
    for item in files:
        first = str(item["path"]).split("/", 1)[0]
        components[first] = components.get(first, 0) + 1
    return dict(sorted(components.items(), key=lambda pair: pair[1], reverse=True)[:12])


def _confidence(files: list[dict[str, Any]], symbols: list[dict[str, Any]], query_terms: set[str]) -> float:
    if not files:
        return 0.0
    score = 0.35 + min(0.25, len(files) / 200) + min(0.25, len(symbols) / 300)
    if query_terms:
        score += 0.15
    return round(min(score, 0.95), 3)


def _terms(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    stop = {"the", "and", "for", "with", "from", "this", "that", "agent"}
    return {token for token in tokens if token not in stop}


def _score_relevance(rel: str, text: str, query_terms: set[str]) -> float:
    if not query_terms:
        return 0.1 if "test" not in rel.lower() else 0.05
    haystack = f"{rel}\n{text[:CODE_FILE_RELEVANCE_CHARS]}".lower()
    hits = sum(1 for term in query_terms if term.lower() in haystack)
    return round(hits / max(len(query_terms), 1), 4)


def _read_indexed_file(repo_root: str, rel_path: str) -> str:
    if not repo_root:
        return ""
    path = Path(repo_root) / rel_path
    if not path.exists() or not path.is_file():
        return ""
    return read_code_text(path)


def _indexed_source_text(repo_root: str, file_row: dict[str, Any]) -> str:
    preview = str(file_row.get("source_preview") or "")
    if preview and _preview_is_current(repo_root, file_row):
        return preview
    return _read_indexed_file(repo_root, str(file_row.get("path") or ""))


def _preview_is_current(repo_root: str, file_row: dict[str, Any]) -> bool:
    if not repo_root:
        return False
    path = Path(repo_root) / str(file_row.get("path") or "")
    if not path.exists() or not path.is_file():
        return False
    current_hash = _file_sha256(path)
    stored_hash = str(file_row.get("hash") or "")
    if current_hash and stored_hash:
        return current_hash == stored_hash
    return _last_modified(path) == str(file_row.get("last_modified") or "")


def _last_modified(path: Path) -> str:
    from datetime import datetime

    return datetime.utcfromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat() + "Z"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files_containing_type(db_path: Path, repository_id: int, files: list[dict[str, Any]], type_name: str, limit: int) -> list[str]:
    from ..database import connect

    with connect(db_path) as conn:
        row = conn.execute("SELECT root_path FROM code_repositories WHERE id=?", (repository_id,)).fetchone()
    repo_root = str(row["root_path"]) if row else ""
    result = []
    for item in files:
        text = _indexed_source_text(repo_root, item)
        if re.search(rf"\b{re.escape(type_name)}\b", text):
            result.append(item["path"])
        if len(result) >= limit:
            break
    return result


def _count_by_file(conn: Any, table: str, file_column: str, *, repository_id: int, file_ids: list[int]) -> dict[int, int]:
    if not file_ids:
        return {}
    placeholders = ",".join("?" for _ in file_ids)
    rows = conn.execute(
        f"""
        SELECT {file_column} AS file_id, COUNT(*) AS count
        FROM {table}
        WHERE repository_id=? AND {file_column} IN ({placeholders})
        GROUP BY {file_column}
        """,
        (repository_id, *file_ids),
    ).fetchall()
    return {int(row["file_id"]): int(row["count"]) for row in rows}


def _snippet_for_terms(text: str, query_terms: set[str], *, limit: int = 240) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    if not query_terms:
        return _trim(" ".join(lines[:4]), limit)
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(term in lowered for term in query_terms):
            start = max(0, index - 1)
            end = min(len(lines), index + 3)
            return _trim(" ".join(lines[start:end]), limit)
    return _trim(" ".join(lines[:4]), limit)


def _compact_symbol(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name", ""),
        "kind": item.get("kind", ""),
        "file": item.get("file_path", item.get("file", "")),
        "file_path": item.get("file_path", item.get("file", "")),
        "start_line": item.get("start_line", 0),
        "end_line": item.get("end_line", 0),
        "signature": item.get("signature", ""),
        "storage_class": item.get("storage_class", ""),
        "metadata": item.get("metadata", {}),
    }


def _compact_include(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": item.get("source_path", ""),
        "to": item.get("resolved_path") or item.get("include_text", ""),
        "kind": "include",
        "include_kind": item.get("include_kind", ""),
        "line": item.get("line", 0),
        "resolved": bool(item.get("resolved_path")),
    }


def _compact_call_edge(item: dict[str, Any], *, direction: str) -> dict[str, Any]:
    return {
        "caller": item.get("caller_name", ""),
        "callee": item.get("callee_name", ""),
        "file": item.get("source_path", ""),
        "line": item.get("line", 0),
        "confidence": item.get("confidence", 0),
        "direction": direction,
    }


def _compact_conditional(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": item.get("source_path", ""),
        "directive": item.get("directive", ""),
        "expression": item.get("expression", ""),
        "start_line": item.get("start_line", 0),
        "end_line": item.get("end_line", 0),
        "macros": item.get("macros", []),
        "variant_key": item.get("variant_key", ""),
    }


def _compact_variable_reference(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": item.get("source_path", ""),
        "function": item.get("function_name", ""),
        "variable": item.get("variable_name", ""),
        "access_type": item.get("access_type", ""),
        "line": item.get("line", 0),
        "confidence": item.get("confidence", 0),
        "snippet": item.get("metadata", {}).get("snippet", ""),
    }


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _trim(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."
