from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..code_style import evaluate_patch_style, get_code_style_profile
from ..database import connect
from ..utils import json_dumps, utc_now
from .index_store import resolve_repo_path


@dataclass
class PatchFile:
    path: str
    hunks: list[list[str]]


def read_style_profile(db_path: Path, project_id: int) -> dict[str, Any]:
    profile = get_code_style_profile(db_path, project_id)
    return {
        "passed": True,
        "profile": profile,
        "evidence_refs": {"style_profile_id": profile.get("id", "")},
    }


def validate_patch(db_path: Path, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    patch_text = _patch_text(db_path, project_id, payload)
    repo = _repo(db_path, project_id)
    parsed = _parse_unified_diff(patch_text)
    apply_result = _apply_patch_to_tree(repo, parsed, dry_run=True)
    style_profile = get_code_style_profile(db_path, project_id)
    style_result = evaluate_patch_style(style_profile, patch_text)
    passed = bool(apply_result["passed"] and style_result.get("passed", False))
    return {
        "passed": passed,
        "dry_run": True,
        "apply_check": apply_result,
        "style_result": style_result,
        "modified_files": [item.path for item in parsed],
        "limitations": [] if passed else ["patch validation failed; inspect apply_check/style_result"],
        "evidence_refs": {"code_files": [item.path for item in parsed]},
    }


def apply_patch(db_path: Path, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    patch_text = _patch_text(db_path, project_id, payload)
    repo = _repo(db_path, project_id)
    parsed = _parse_unified_diff(patch_text)
    dry_run = bool(payload.get("dry_run"))
    result = _apply_patch_to_tree(repo, parsed, dry_run=dry_run)
    if result["passed"] and not dry_run:
        _audit(
            db_path,
            project_id,
            "PATCH_APPLIED",
            "Applied confirmed candidate patch to local code repository",
            {
                "modified_files": result["modified_files"],
                "artifact_id": payload.get("artifact_id"),
                "reviewer": payload.get("reviewer", "local_user"),
                "comment": payload.get("comment", ""),
            },
        )
    return {
        **result,
        "dry_run": dry_run,
        "evidence_refs": {"code_files": result.get("modified_files", [])},
    }


def run_allowed_command(db_path: Path, project_id: int, command_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo = _repo(db_path, project_id)
    inputs = _project_inputs(db_path, project_id)
    key = {
        "run_build": "personal_build_command",
        "run_tests": "personal_test_command",
        "run_static_analysis": "personal_static_analysis_command",
    }.get(command_kind, "")
    configured = str(inputs.get(key) or "").strip()
    requested = str(payload.get("command") or configured).strip()
    timeout_s = int(payload.get("timeout_s") or inputs.get("personal_tool_timeout_s") or 120)
    if not configured:
        return _command_failed(command_kind, requested, f"{key} is not configured")
    if requested != configured:
        return _command_failed(command_kind, requested, "requested command is not in the personal allowlist")
    args = _split_command(requested)
    if not args:
        return _command_failed(command_kind, requested, "command is empty")
    started = utc_now()
    try:
        completed = subprocess.run(
            args,
            cwd=repo,
            shell=False,
            text=True,
            capture_output=True,
            timeout=max(1, min(timeout_s, 1800)),
        )
        output = _trim_output((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""))
        passed = completed.returncode == 0
        result = {
            "passed": passed,
            "command_kind": command_kind,
            "command": requested,
            "argv": args,
            "cwd": str(repo),
            "returncode": completed.returncode,
            "started_at": started,
            "completed_at": utc_now(),
            "output_summary": output,
            "limitations": [] if passed else ["command returned non-zero exit status"],
            "evidence_refs": {"validation": command_kind},
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "passed": False,
            "command_kind": command_kind,
            "command": requested,
            "argv": args,
            "cwd": str(repo),
            "returncode": -1,
            "started_at": started,
            "completed_at": utc_now(),
            "output_summary": _trim_output(str(exc)),
            "limitations": ["command timed out"],
            "evidence_refs": {"validation": command_kind},
        }
    _audit(db_path, project_id, command_kind.upper(), f"Ran {command_kind} for personal codebase", result)
    return result


def _repo(db_path: Path, project_id: int) -> Path:
    repo = resolve_repo_path(db_path, project_id, "")
    if not repo:
        raise ValueError("code_repo_path is not configured")
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"code repository does not exist: {repo}")
    return repo


def _patch_text(db_path: Path, project_id: int, payload: dict[str, Any]) -> str:
    text = str(payload.get("patch_text") or "")
    if text.strip():
        return text
    draft_uid = str(payload.get("draft_uid") or "").strip()
    if draft_uid:
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT r.content
                FROM personal_drafts d
                JOIN personal_draft_revisions r
                  ON r.draft_uid=d.draft_uid AND r.revision_index=d.current_revision
                WHERE d.project_id=? AND d.draft_uid=? AND d.document_type='c_code_diff'
                """,
                (project_id, draft_uid),
            ).fetchone()
        if not row:
            raise ValueError("candidate patch draft was not found")
        return str(row["content"] or "")
    artifact_id = int(payload.get("artifact_id") or 0)
    if artifact_id <= 0:
        raise ValueError("patch_text, draft_uid, or artifact_id is required")
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT af.file_path
            FROM artifact_files af JOIN artifacts a ON a.id=af.artifact_id
            WHERE a.id=? AND a.project_id=? AND a.artifact_type='c_code_diff'
            ORDER BY af.id DESC LIMIT 1
            """,
            (artifact_id, project_id),
        ).fetchone()
    if not row:
        raise ValueError("candidate patch artifact was not found")
    path = Path(str(row["file_path"]))
    if not path.exists() or not path.is_file():
        raise ValueError("candidate patch file is missing")
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_unified_diff(patch_text: str) -> list[PatchFile]:
    files: list[PatchFile] = []
    current: PatchFile | None = None
    current_hunk: list[str] | None = None
    for line in patch_text.splitlines():
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            raw = line[4:].strip()
            path = raw[2:] if raw.startswith("b/") else raw
            current = PatchFile(path=path, hunks=[])
            files.append(current)
            current_hunk = None
            continue
        if line.startswith("@@ "):
            if current is None:
                raise ValueError("unified diff hunk appears before file header")
            current_hunk = [line]
            current.hunks.append(current_hunk)
            continue
        if current_hunk is not None and (line.startswith(" ") or line.startswith("+") or line.startswith("-") or line == "\\ No newline at end of file"):
            current_hunk.append(line)
    if not files:
        raise ValueError("patch contains no unified diff file entries")
    return files


def _apply_patch_to_tree(repo: Path, files: list[PatchFile], *, dry_run: bool) -> dict[str, Any]:
    modified: list[str] = []
    errors: list[str] = []
    writes: list[tuple[Path, str]] = []
    root = repo.resolve()
    for patch_file in files:
        target = (repo / patch_file.path).resolve()
        if target != root and root not in target.parents:
            errors.append(f"{patch_file.path}: target escapes code repository")
            continue
        if not target.exists() or not target.is_file():
            errors.append(f"{patch_file.path}: target file does not exist")
            continue
        original = target.read_text(encoding="utf-8", errors="replace")
        try:
            updated = _apply_file_hunks(original, patch_file.hunks)
        except ValueError as exc:
            errors.append(f"{patch_file.path}: {exc}")
            continue
        if updated != original:
            modified.append(patch_file.path)
            writes.append((target, updated))
    if errors:
        return {"passed": False, "modified_files": modified, "errors": errors, "limitations": errors}
    if not dry_run:
        for target, updated in writes:
            target.write_text(updated, encoding="utf-8")
    return {
        "passed": True,
        "modified_files": modified,
        "file_count": len(modified),
        "errors": [],
        "limitations": [] if modified else ["patch was valid but did not change any file"],
    }


def _apply_file_hunks(original: str, hunks: list[list[str]]) -> str:
    lines = original.splitlines(keepends=True)
    cursor = 0
    out: list[str] = []
    for hunk in hunks:
        body = [line for line in hunk[1:] if line != "\\ No newline at end of file"]
        old_block = [_line_payload(line) for line in body if line.startswith(" ") or line.startswith("-")]
        new_block = [_line_payload(line) for line in body if line.startswith(" ") or line.startswith("+")]
        pos = _find_block(lines, old_block, cursor)
        if pos < 0:
            raise ValueError("hunk context did not match current file")
        out.extend(lines[cursor:pos])
        out.extend(new_block)
        cursor = pos + len(old_block)
    out.extend(lines[cursor:])
    return "".join(out)


def _line_payload(diff_line: str) -> str:
    payload = diff_line[1:]
    return payload + "\n"


def _find_block(lines: list[str], block: list[str], start: int) -> int:
    if not block:
        return start
    max_start = len(lines) - len(block)
    for idx in range(start, max_start + 1):
        if lines[idx : idx + len(block)] == block:
            return idx
    return -1


def _project_inputs(db_path: Path, project_id: int) -> dict[str, str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT input_key, value FROM project_inputs WHERE project_id=? AND status='active'", (project_id,)).fetchall()
    return {str(row["input_key"]): str(row["value"]) for row in rows}


def _split_command(command: str) -> list[str]:
    return shlex.split(command, posix=False)


def _trim_output(text: str, limit: int = 12000) -> str:
    value = text.strip()
    return value if len(value) <= limit else value[:limit] + "\n...[truncated]"


def _command_failed(command_kind: str, command: str, reason: str) -> dict[str, Any]:
    return {
        "passed": False,
        "command_kind": command_kind,
        "command": command,
        "returncode": None,
        "output_summary": "",
        "limitations": [reason],
        "evidence_refs": {"validation": command_kind},
    }


def _audit(db_path: Path, project_id: int, event_type: str, message: str, payload: dict[str, Any]) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_events(project_id, event_type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (project_id, event_type, message, json_dumps(payload), utc_now()),
        )
