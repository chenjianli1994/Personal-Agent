from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..database import connect
from ..utils import json_dumps, utc_now
from .c_parser import parse_code_file
from .scanner import scan_code_repository
from .schemas import (
    ParsedCallEdge,
    ParsedConditionalBlock,
    ParsedCodeFile,
    ParsedInclude,
    ParsedSymbol,
    ParsedVariableReference,
    ScannedCodeFile,
)


def resolve_repo_path(db_path: Path, project_id: int, repo_path: str = "") -> Path | None:
    configured = _configured_repo_path(db_path, project_id)
    if not configured:
        return None
    if not repo_path.strip():
        return configured
    candidate = Path(repo_path).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    root = configured.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("code repository path must stay within the configured project code_repo_path")
    return candidate


def build_and_store_index(
    db_path: Path,
    project_id: int,
    *,
    repo_path: str = "",
    max_files: int = 160,
    skip_dirs: list[str] | None = None,
    batch_size: int = 0,
) -> dict[str, Any]:
    repo = resolve_repo_path(db_path, project_id, repo_path)
    if not repo:
        return _missing_index(project_id, "project input code_repo_path is not configured")
    scan = scan_code_repository(repo, max_files=max_files, skip_dirs=skip_dirs, batch_size=batch_size)
    if not repo.exists() or not repo.is_dir():
        return _missing_index(project_id, scan["limitations"][0] if scan["limitations"] else f"code repository does not exist: {repo}", repo)

    now = utc_now()
    changed_file_count = 0
    reused_file_count = 0
    deleted_file_count = 0
    parsed_by_file: dict[str, ParsedCodeFile] = {}
    parser_names: list[str] = []
    parser_confidences: list[float] = []
    parser_limitations: list[str] = []
    with connect(db_path) as conn:
        repo_id = _upsert_repository(conn, project_id, repo, now)
        run_id = _start_index_run(conn, repo_id, now)
        previous_files = _repository_file_state(conn, repo_id)
        scanned_by_path = {file.rel_path: file for file in scan["files"]}
        deleted_paths = [path for path in previous_files if path not in scanned_by_path]
        for path in deleted_paths:
            _delete_file_index(conn, repo_id, int(previous_files[path]["id"]))
            deleted_file_count += 1

        file_ids: dict[str, int] = {}
        for file in scan["files"]:
            previous = previous_files.get(file.rel_path)
            if previous and previous["hash"] == file.hash and previous["last_modified"] == file.last_modified and _has_parser_metadata(previous):
                file_ids[file.rel_path] = int(previous["id"])
                reused_file_count += 1
                parser_names.append(str(previous.get("parser") or "regex"))
                parser_confidences.append(float(previous.get("parser_confidence") or 0.0))
                continue
            parsed = parse_code_file(file.text, file.rel_path, file.suffix)
            parsed_by_file[file.rel_path] = parsed
            parser_names.append(parsed.parser)
            parser_confidences.append(parsed.parser_confidence)
            parser_limitations.extend(parsed.limitations)
            if previous:
                _delete_file_relations(conn, repo_id, int(previous["id"]))
                file_ids[file.rel_path] = _update_file(conn, repo_id, int(previous["id"]), file, parsed, now)
            else:
                file_ids[file.rel_path] = _insert_file(conn, repo_id, file, parsed, now)
            changed_file_count += 1

        symbol_count = 0
        include_count = 0
        for rel_path, parsed in parsed_by_file.items():
            file_id = file_ids[rel_path]
            for symbol in _dedupe_symbols(parsed.symbols):
                _insert_symbol(conn, repo_id, file_id, symbol)
                symbol_count += 1
            for include in parsed.includes:
                _insert_include(conn, repo_id, file_id, include, _resolve_include_id(include, rel_path, file_ids))
                include_count += 1
            for edge in _dedupe_call_edges(parsed.call_edges):
                _insert_call_edge(conn, repo_id, file_id, edge)
            for block in parsed.conditional_blocks:
                _insert_conditional_block(conn, repo_id, file_id, block)
            for ref in parsed.variable_references:
                _insert_variable_reference(conn, repo_id, file_id, ref)
        totals = _repository_totals(conn, repo_id)
        parser = _select_parser(parser_names)
        parser_confidence = _average_confidence(parser_confidences)
        limitations = _unique([*scan["limitations"], *parser_limitations])
        run_stats = {
            "changed_file_count": changed_file_count,
            "reused_file_count": reused_file_count,
            "skipped_unchanged_count": reused_file_count,
            "deleted_file_count": deleted_file_count,
            "skipped_after_limit": int(scan["skipped_after_limit"]),
            "skipped_by_dir": int(scan.get("skipped_by_dir", 0)),
            "batch_size": int(scan.get("batch_size", 0)),
            "batch_count": int(scan.get("batch_count", 0)),
            "parser": parser,
            "parser_confidence": parser_confidence,
            "parser_capabilities": {
                "fallback_active": parser == "regex",
                "parsers_seen": _unique(parser_names),
            },
            "skip_dirs": sorted(set(skip_dirs or [])),
        }
        _finish_index_run(conn, run_id, "completed", totals["file_count"], totals["symbol_count"], totals["include_count"], limitations, now, run_stats)
        conn.execute("UPDATE code_repositories SET last_indexed_at=?, updated_at=? WHERE id=?", (now, now, repo_id))

    return {
        "project_id": project_id,
        "repository_id": repo_id,
        "code_repo_path": str(repo),
        "exists": True,
        "file_count": totals["file_count"],
        "symbol_count": totals["symbol_count"],
        "dependency_count": totals["include_count"],
        "include_count": totals["include_count"],
        "test_file_count": sum(1 for file in scan["files"] if file.file_type == "test"),
        "skipped_after_limit": int(scan["skipped_after_limit"]),
        "skipped_by_dir": int(scan.get("skipped_by_dir", 0)),
        "limitations": limitations,
        "parser": parser,
        "parser_confidence": parser_confidence,
        "changed_file_count": changed_file_count,
        "reused_file_count": reused_file_count,
        "skipped_unchanged_count": reused_file_count,
        "deleted_file_count": deleted_file_count,
        "batch_size": int(scan.get("batch_size", 0)),
        "batch_count": int(scan.get("batch_count", 0)),
        "index_run_id": run_id,
        "indexed_at": now,
        "index_run": run_stats,
        "files": scan["files"],
    }


def latest_repository(db_path: Path, project_id: int) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM code_repositories WHERE project_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def repository_files(db_path: Path, repository_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM code_files WHERE repository_id=? ORDER BY path",
            (repository_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def repository_symbols(db_path: Path, repository_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.*, f.path AS file_path, f.file_type
            FROM code_symbols s JOIN code_files f ON f.id=s.file_id
            WHERE s.repository_id=?
            ORDER BY s.name, s.start_line
            """,
            (repository_id,),
        ).fetchall()
    return [_decode_symbol(dict(row)) for row in rows]


def repository_includes(db_path: Path, repository_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT i.*, src.path AS source_path, dst.path AS resolved_path
            FROM code_includes i
            JOIN code_files src ON src.id=i.source_file_id
            LEFT JOIN code_files dst ON dst.id=i.resolved_file_id
            WHERE i.repository_id=?
            ORDER BY src.path, i.line
            """,
            (repository_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def repository_call_edges(db_path: Path, repository_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.*, f.path AS source_path
            FROM code_call_edges e JOIN code_files f ON f.id=e.file_id
            WHERE e.repository_id=?
            ORDER BY e.caller_name, e.line
            """,
            (repository_id,),
        ).fetchall()
    return [_decode_json_fields(dict(row), {"metadata_json": ("metadata", {})}) for row in rows]


def repository_conditional_blocks(db_path: Path, repository_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT b.*, f.path AS source_path
            FROM code_conditional_blocks b JOIN code_files f ON f.id=b.file_id
            WHERE b.repository_id=?
            ORDER BY f.path, b.start_line
            """,
            (repository_id,),
        ).fetchall()
    return [_decode_json_fields(dict(row), {"macros_json": ("macros", []), "metadata_json": ("metadata", {})}) for row in rows]


def repository_variable_references(db_path: Path, repository_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.*, f.path AS source_path
            FROM code_variable_references r JOIN code_files f ON f.id=r.file_id
            WHERE r.repository_id=?
            ORDER BY r.variable_name, r.line
            """,
            (repository_id,),
        ).fetchall()
    return [_decode_json_fields(dict(row), {"metadata_json": ("metadata", {})}) for row in rows]


def _configured_repo_path(db_path: Path, project_id: int) -> Path | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM project_inputs WHERE project_id=? AND input_key='code_repo_path' AND status='active'",
            (project_id,),
        ).fetchone()
    if not row or not str(row["value"]).strip():
        return None
    path = Path(str(row["value"]).strip()).expanduser()
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _missing_index(project_id: int, reason: str, repo_path: Path | None = None) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "code_repo_path": str(repo_path or ""),
        "exists": False,
        "file_count": 0,
        "symbol_count": 0,
        "dependency_count": 0,
        "include_count": 0,
        "test_file_count": 0,
        "skipped_after_limit": 0,
        "skipped_by_dir": 0,
        "limitations": [reason],
        "parser": "regex",
        "parser_confidence": 0.0,
        "changed_file_count": 0,
        "reused_file_count": 0,
        "skipped_unchanged_count": 0,
        "deleted_file_count": 0,
        "batch_size": 0,
        "batch_count": 0,
        "files": [],
    }


def _upsert_repository(conn, project_id: int, repo: Path, now: str) -> int:
    conn.execute(
        """
        INSERT INTO code_repositories(project_id, root_path, status, created_at, updated_at)
        VALUES (?, ?, 'active', ?, ?)
        ON CONFLICT(project_id, root_path) DO UPDATE SET status='active', updated_at=excluded.updated_at
        """,
        (project_id, str(repo), now, now),
    )
    return int(conn.execute("SELECT id FROM code_repositories WHERE project_id=? AND root_path=?", (project_id, str(repo))).fetchone()["id"])


def _start_index_run(conn, repo_id: int, now: str) -> int:
    conn.execute(
        """
        INSERT INTO code_index_runs(repository_id, status, started_at, completed_at)
        VALUES (?, 'running', ?, '')
        """,
        (repo_id, now),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _finish_index_run(
    conn,
    run_id: int,
    status: str,
    file_count: int,
    symbol_count: int,
    include_count: int,
    limitations: list[str],
    now: str,
    run_stats: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE code_index_runs
        SET status=?,
            file_count=?,
            symbol_count=?,
            include_count=?,
            changed_file_count=?,
            reused_file_count=?,
            skipped_unchanged_count=?,
            deleted_file_count=?,
            skipped_after_limit=?,
            skipped_by_dir=?,
            batch_size=?,
            batch_count=?,
            parser=?,
            parser_confidence=?,
            warning_count=?,
            limitations_json=?,
            metadata_json=?,
            completed_at=?
        WHERE id=?
        """,
        (
            status,
            file_count,
            symbol_count,
            include_count,
            int(run_stats.get("changed_file_count", 0)),
            int(run_stats.get("reused_file_count", 0)),
            int(run_stats.get("skipped_unchanged_count", 0)),
            int(run_stats.get("deleted_file_count", 0)),
            int(run_stats.get("skipped_after_limit", 0)),
            int(run_stats.get("skipped_by_dir", 0)),
            int(run_stats.get("batch_size", 0)),
            int(run_stats.get("batch_count", 0)),
            str(run_stats.get("parser") or "regex"),
            float(run_stats.get("parser_confidence") or 0.0),
            len(limitations),
            json_dumps(limitations),
            json_dumps(run_stats),
            now,
            run_id,
        ),
    )


def _replace_repository_index(conn, repo_id: int) -> None:
    conn.execute("DELETE FROM code_variable_references WHERE repository_id=?", (repo_id,))
    conn.execute("DELETE FROM code_conditional_blocks WHERE repository_id=?", (repo_id,))
    conn.execute("DELETE FROM code_call_edges WHERE repository_id=?", (repo_id,))
    conn.execute("DELETE FROM code_includes WHERE repository_id=?", (repo_id,))
    conn.execute("DELETE FROM code_symbols WHERE repository_id=?", (repo_id,))
    conn.execute("DELETE FROM code_files WHERE repository_id=?", (repo_id,))


def _repository_file_state(conn, repo_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT * FROM code_files WHERE repository_id=?", (repo_id,)).fetchall()
    return {str(row["path"]): dict(row) for row in rows}


def _has_parser_metadata(file_row: dict[str, Any]) -> bool:
    return bool(str(file_row.get("parser") or "").strip()) and float(file_row.get("parser_confidence") or 0.0) > 0


def _delete_file_index(conn, repo_id: int, file_id: int) -> None:
    _delete_file_relations(conn, repo_id, file_id)
    conn.execute("DELETE FROM code_files WHERE repository_id=? AND id=?", (repo_id, file_id))


def _delete_file_relations(conn, repo_id: int, file_id: int) -> None:
    conn.execute("UPDATE code_includes SET resolved_file_id=NULL WHERE repository_id=? AND resolved_file_id=?", (repo_id, file_id))
    conn.execute("DELETE FROM code_variable_references WHERE repository_id=? AND file_id=?", (repo_id, file_id))
    conn.execute("DELETE FROM code_conditional_blocks WHERE repository_id=? AND file_id=?", (repo_id, file_id))
    conn.execute("DELETE FROM code_call_edges WHERE repository_id=? AND file_id=?", (repo_id, file_id))
    conn.execute("DELETE FROM code_includes WHERE repository_id=? AND source_file_id=?", (repo_id, file_id))
    conn.execute("DELETE FROM code_symbols WHERE repository_id=? AND file_id=?", (repo_id, file_id))


def _repository_totals(conn, repo_id: int) -> dict[str, int]:
    return {
        "file_count": int(conn.execute("SELECT COUNT(*) FROM code_files WHERE repository_id=?", (repo_id,)).fetchone()[0]),
        "symbol_count": int(conn.execute("SELECT COUNT(*) FROM code_symbols WHERE repository_id=?", (repo_id,)).fetchone()[0]),
        "include_count": int(conn.execute("SELECT COUNT(*) FROM code_includes WHERE repository_id=?", (repo_id,)).fetchone()[0]),
    }


def _insert_file(conn, repo_id: int, file: ScannedCodeFile, parsed: ParsedCodeFile, now: str) -> int:
    conn.execute(
        """
        INSERT INTO code_files(repository_id, path, language, file_type, hash, line_count, last_modified, last_indexed_at, parser, parser_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (repo_id, file.rel_path, file.language, file.file_type, file.hash, file.line_count, file.last_modified, now, parsed.parser, parsed.parser_confidence),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _update_file(conn, repo_id: int, file_id: int, file: ScannedCodeFile, parsed: ParsedCodeFile, now: str) -> int:
    conn.execute(
        """
        UPDATE code_files
        SET language=?, file_type=?, hash=?, line_count=?, last_modified=?, last_indexed_at=?, parser=?, parser_confidence=?
        WHERE id=? AND repository_id=?
        """,
        (file.language, file.file_type, file.hash, file.line_count, file.last_modified, now, parsed.parser, parsed.parser_confidence, file_id, repo_id),
    )
    return file_id


def _insert_symbol(conn, repo_id: int, file_id: int, symbol: ParsedSymbol) -> None:
    conn.execute(
        """
        INSERT INTO code_symbols(repository_id, file_id, name, kind, signature, storage_class, start_line, end_line, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repo_id,
            file_id,
            symbol.name,
            symbol.kind,
            symbol.signature,
            symbol.storage_class,
            symbol.start_line,
            symbol.end_line,
            json_dumps(symbol.metadata),
        ),
    )


def _insert_include(conn, repo_id: int, file_id: int, include: ParsedInclude, resolved_file_id: int | None) -> None:
    conn.execute(
        """
        INSERT INTO code_includes(repository_id, source_file_id, include_text, resolved_file_id, include_kind, line)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (repo_id, file_id, include.include_text, resolved_file_id, include.include_kind, include.line),
    )


def _insert_call_edge(conn, repo_id: int, file_id: int, edge: ParsedCallEdge) -> None:
    conn.execute(
        """
        INSERT INTO code_call_edges(repository_id, file_id, caller_name, callee_name, line, confidence, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (repo_id, file_id, edge.caller_name, edge.callee_name, edge.line, edge.confidence, json_dumps(edge.metadata)),
    )


def _insert_conditional_block(conn, repo_id: int, file_id: int, block: ParsedConditionalBlock) -> None:
    conn.execute(
        """
        INSERT INTO code_conditional_blocks(
            repository_id, file_id, directive, expression, start_line, end_line, macros_json, variant_key, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repo_id,
            file_id,
            block.directive,
            block.expression,
            block.start_line,
            block.end_line,
            json_dumps(block.macros),
            block.variant_key,
            json_dumps(block.metadata),
        ),
    )


def _insert_variable_reference(conn, repo_id: int, file_id: int, ref: ParsedVariableReference) -> None:
    conn.execute(
        """
        INSERT INTO code_variable_references(
            repository_id, file_id, function_name, variable_name, access_type, line, confidence, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (repo_id, file_id, ref.function_name, ref.variable_name, ref.access_type, ref.line, ref.confidence, json_dumps(ref.metadata)),
    )


def _resolve_include_id(include: ParsedInclude, source_rel_path: str, file_ids: dict[str, int]) -> int | None:
    source_dir = Path(source_rel_path).parent
    candidates = [
        str((source_dir / include.include_text).as_posix()).lstrip("./"),
        include.include_text.replace("\\", "/"),
    ]
    basename = Path(include.include_text).name.lower()
    for candidate in candidates:
        if candidate in file_ids:
            return file_ids[candidate]
    matches = [file_id for path, file_id in file_ids.items() if Path(path).name.lower() == basename]
    return matches[0] if len(matches) == 1 else None


def _dedupe_symbols(symbols: list[ParsedSymbol]) -> list[ParsedSymbol]:
    seen: set[tuple[str, str, int]] = set()
    deduped: list[ParsedSymbol] = []
    for symbol in symbols:
        key = (symbol.name, symbol.kind, symbol.start_line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(symbol)
    return deduped


def _dedupe_call_edges(edges: list[ParsedCallEdge]) -> list[ParsedCallEdge]:
    seen: set[tuple[str, str, int]] = set()
    deduped: list[ParsedCallEdge] = []
    for edge in edges:
        key = (edge.caller_name, edge.callee_name, edge.line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def _select_parser(parser_names: list[str]) -> str:
    names = _unique(parser_names)
    if not names:
        return "regex"
    if len(names) == 1:
        return names[0]
    if "tree-sitter-c" in names:
        return "mixed:tree-sitter-c+regex"
    return "mixed:" + "+".join(names[:3])


def _average_confidence(values: list[float]) -> float:
    usable = [float(value) for value in values if float(value or 0) > 0]
    if not usable:
        return 0.0
    return round(sum(usable) / len(usable), 3)


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _decode_symbol(row: dict[str, Any]) -> dict[str, Any]:
    try:
        row["metadata"] = json.loads(row.pop("metadata_json", "") or "{}")
    except Exception:
        row["metadata"] = {}
    return row


def _decode_json_fields(row: dict[str, Any], fields: dict[str, tuple[str, Any]]) -> dict[str, Any]:
    for source, (target, default) in fields.items():
        try:
            row[target] = json.loads(row.pop(source, "") or json_dumps(default))
        except Exception:
            row[target] = default
    return row
