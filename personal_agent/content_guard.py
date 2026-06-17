from __future__ import annotations

import re


FORBIDDEN_PERSONAL_TERMS = (
    "ASPICE",
    "SYS.",
    "SWE.",
    "THM-SWE",
    "Gate",
    "baseline",
    "基线",
    "评审闭环",
    "正式 artifact",
)


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
        elif term in content:
            hits.append(term)
    return hits


def assert_personal_content_clean(text: str, *, label: str = "content") -> None:
    hits = personal_forbidden_hits(text)
    if hits:
        raise ValueError(f"{label} contains personal Agent forbidden terms: {', '.join(sorted(set(hits)))}")
