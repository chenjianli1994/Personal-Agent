from __future__ import annotations

from pathlib import Path


CODE_FILE_RELEVANCE_CHARS = 10000
CODE_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1")


def decode_code_bytes(data: bytes) -> str:
    for encoding in CODE_TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_code_text(path: Path) -> str:
    return decode_code_bytes(path.read_bytes())


def source_preview(text: str) -> str:
    return text[:CODE_FILE_RELEVANCE_CHARS]
