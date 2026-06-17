from __future__ import annotations

import re
from typing import Any


FORBIDDEN_PERSONAL_TERMS = (
    "ASPICE",
    "SYS.",
    "SWE.",
    "THM-SWE",
    "Gate",
    "baseline",
    "aspice_lesson",
    "quality_gate_profile",
    "aspice_trace_impact",
    "基线",
    "评审闭环",
    "正式 artifact",
)

RETIRED_PROJECT_INPUT_KEYS = (
    "code_repo_path",
    "template_library_path",
    "knowledge_library_path",
    "quality_gate_profile",
)

SEARCHABLE_KNOWLEDGE_FIELDS = ("title", "content", "source_ref", "source_title", "source_uri")
SEARCHABLE_SEQUENCE_FIELDS = ("tags", "process_codes", "applicable_process")


def personal_forbidden_hits(text: str) -> list[str]:
    content = text or ""
    hits: list[str] = []
    for term in FORBIDDEN_PERSONAL_TERMS:
        if term == "Gate":
            if re.search(r"\bGate\b", content, flags=re.IGNORECASE):
                hits.append(term)
        elif term in {"ASPICE", "baseline"}:
            if re.search(re.escape(term), content, flags=re.IGNORECASE):
                hits.append(term)
        elif term in {"SYS.", "SWE.", "THM-SWE"}:
            if term in content:
                hits.append(term)
        elif re.search(re.escape(term), content, flags=re.IGNORECASE):
            hits.append(term)
    return hits


def assert_personal_content_clean(text: str, *, label: str = "content") -> None:
    hits = personal_forbidden_hits(text)
    if hits:
        raise ValueError(f"{label} contains personal Agent forbidden terms: {', '.join(sorted(set(hits)))}")


def assert_personal_payload_clean(payload: dict[str, Any], *, label: str = "payload") -> None:
    hits = personal_payload_forbidden_hits(payload)
    if hits:
        formatted = ", ".join(sorted({item["term"] for item in hits}))
        raise ValueError(f"{label} contains personal Agent forbidden terms: {formatted}")


def personal_payload_forbidden_hits(payload: dict[str, Any]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for field in SEARCHABLE_KNOWLEDGE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str):
            continue
        for term in personal_forbidden_hits(value):
            hits.append({"field": field, "term": term})
    for field in SEARCHABLE_SEQUENCE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, str):
                continue
            for term in personal_forbidden_hits(item):
                hits.append({"field": field, "term": term})
    return hits
