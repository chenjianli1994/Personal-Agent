from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .schemas import CODE_EXTENSIONS, SKIP_DIRS, TEST_HINTS, ScannedCodeFile


def scan_code_repository(repo_path: Path, *, max_files: int = 160, skip_dirs: list[str] | None = None, batch_size: int = 0) -> dict[str, Any]:
    repo = repo_path.resolve()
    files: list[ScannedCodeFile] = []
    skipped_after_limit = 0
    skipped_by_dir = 0
    limitations: list[str] = []
    effective_skip_dirs = set(SKIP_DIRS)
    for item in skip_dirs or []:
        cleaned = str(item).strip().replace("\\", "/").strip("/")
        if cleaned:
            effective_skip_dirs.add(cleaned)
    if not repo.exists() or not repo.is_dir():
        return {"files": [], "skipped_after_limit": 0, "skipped_by_dir": 0, "limitations": [f"code repository does not exist: {repo}"]}

    for path in sorted(repo.rglob("*")):
        if _is_skipped(repo, path, effective_skip_dirs):
            if path.is_file():
                skipped_by_dir += 1
            continue
        if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        if len(files) >= max_files:
            skipped_after_limit += 1
            continue
        text = _safe_read(path)
        if not text.strip():
            continue
        stat = path.stat()
        suffix = path.suffix.lower()
        rel = str(path.relative_to(repo)).replace("\\", "/")
        files.append(
            ScannedCodeFile(
                path=path,
                rel_path=rel,
                suffix=suffix,
                language=_language_for_suffix(suffix),
                file_type=_file_type(rel, text, suffix),
                hash=_sha256_bytes(path.read_bytes()),
                line_count=len(text.splitlines()),
                last_modified=datetime.utcfromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat() + "Z",
                text=text,
            )
        )
    if skipped_after_limit:
        limitations.append(f"scan capped before all files were indexed: skipped_after_limit={skipped_after_limit}")
    if skipped_by_dir:
        limitations.append(f"scan skipped files by configured directories: skipped_by_dir={skipped_by_dir}")
    if batch_size and len(files) > batch_size:
        limitations.append(f"indexing will be processed in batches: batch_size={batch_size}, batch_count={_batch_count(len(files), batch_size)}")
    if not files:
        limitations.append("no supported text code files were found")
    if not (repo / ".git").exists():
        limitations.append("repository has no .git directory; merge safety should use workspace copy and backups")
    return {
        "files": files,
        "skipped_after_limit": skipped_after_limit,
        "skipped_by_dir": skipped_by_dir,
        "batch_size": max(0, int(batch_size or 0)),
        "batch_count": _batch_count(len(files), batch_size),
        "limitations": limitations,
    }


def _is_skipped(root: Path, path: Path, skip_dirs: set[str]) -> bool:
    try:
        parts = [part.replace("\\", "/") for part in path.relative_to(root).parts]
    except ValueError:
        parts = [part.replace("\\", "/") for part in path.parts]
    rel = "/".join(parts)
    return any(part in skip_dirs for part in parts) or any(rel == item or rel.startswith(f"{item}/") for item in skip_dirs if "/" in item)


def _safe_read(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"):
        try:
            return data.decode(encoding, errors="ignore")
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _batch_count(file_count: int, batch_size: int) -> int:
    if batch_size <= 0 or file_count <= 0:
        return 0
    return (file_count + batch_size - 1) // batch_size


def _language_for_suffix(suffix: str) -> str:
    return {
        ".c": "C",
        ".h": "C/C++ header",
        ".cpp": "C++",
        ".hpp": "C++ header",
        ".cc": "C++",
        ".hh": "C++ header",
        ".py": "Python",
        ".java": "Java",
        ".ts": "TypeScript",
        ".tsx": "TypeScript React",
        ".js": "JavaScript",
        ".jsx": "JavaScript React",
    }.get(suffix, suffix.lstrip("."))


def _file_type(rel: str, text: str, suffix: str) -> str:
    lowered = rel.lower()
    if any(hint in lowered for hint in TEST_HINTS) or "assert" in text[:4000].lower() or "unity_begin" in text[:4000].lower():
        return "test"
    if suffix in {".h", ".hpp", ".hh"}:
        return "interface"
    return "implementation"
