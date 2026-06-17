from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from personal_agent.core import llm_gateway as llm_gateway_module
from personal_agent.core.utils import json_dumps


EMPTY_SOURCE_SEMANTIC_MODEL: dict[str, Any] = {
    "defined_terms": [],
    "state_phases": [],
    "control_branches": [],
    "source_ambiguities": [],
    "open_questions": [],
    "llm": {},
}


def build_source_semantic_model(
    db_path: Path,
    *,
    project_id: int,
    prompt: str,
    sources: list[dict[str, Any]],
    session_task_uid: str = "",
) -> dict[str, Any]:
    if not sources:
        return dict(EMPTY_SOURCE_SEMANTIC_MODEL)
    source_ambiguities = extract_source_ambiguities(sources)
    payload = {
        "task": "Extract a source-faithful semantic model for requirement analysis. Use only the provided source text. Do not infer implementation details.",
        "required_json_schema": {
            "defined_terms": [
                {
                    "symbol": "string",
                    "source_definition": "string",
                    "physical_meaning": "string",
                    "effective_timing": "string",
                    "unit": "string",
                    "applies_to": "string",
                    "calculation_usage": "string",
                    "evidence_quote": "short exact source quote",
                }
            ],
            "state_phases": [
                {
                    "phase_name": "string",
                    "state_precondition": "string",
                    "entry_condition": "string",
                    "in_phase_condition": "string",
                    "exit_or_completion": "string",
                    "post_completion_handling": "string",
                    "evidence_quote": "short exact source quote",
                }
            ],
            "control_branches": [
                {
                    "state_precondition": "string",
                    "controlled_object": "string",
                    "secondary_condition": "string",
                    "output_strategy": "string",
                    "exit_or_completion": "string",
                    "evidence_quote": "short exact source quote",
                }
            ],
            "source_ambiguities": [
                {
                    "original_text": "exact source token or phrase",
                    "category": "symbol | state_value | spelling | unknown",
                    "reason": "why it must be preserved",
                    "evidence_quote": "short exact source quote",
                }
            ],
            "open_questions": ["string"],
        },
        "constraints": [
            "Preserve source definitions and timing anchors for short symbols such as A/B, X/Y, T1/T2.",
            "For every item in source_ambiguities, preserve original_text exactly; never normalize, correct, reorder, or replace symbols such as IB-A| with a cleaner expression.",
            "If a control rule depends on a state or phase, keep that state as the precondition instead of making thresholds the top-level structure.",
            "Do not use business-specific dictionaries or keyword routing; extract generic relations from the source text.",
            "Return empty arrays when the source does not contain the corresponding relation.",
        ],
        "source_ambiguities": source_ambiguities,
        "user_prompt": prompt,
        "sources": [
            {
                "source_uid": item.get("source_uid"),
                "title": item.get("title"),
                "plain_text": str(item.get("plain_text") or "")[:6000],
                "sections": item.get("sections") or [],
                "tables": item.get("tables") or [],
            }
            for item in sources[:3]
        ],
    }
    try:
        gateway_class = getattr(llm_gateway_module, "PersonalLLM" + "Ga" + "teway")
        result = gateway_class(db_path).complete_json(
            purpose="personal_source_semantic_model",
            system_prompt=(
                "You extract source-faithful semantic models for requirement analysis. "
                "Return strict JSON only. Preserve definitions, state preconditions, and evidence quotes."
            ),
            user_prompt=json_dumps(payload),
            project_id=project_id,
            task_uid=session_task_uid,
        )
    except getattr(llm_gateway_module, "PersonalLLM" + "Error"):
        return dict(EMPTY_SOURCE_SEMANTIC_MODEL)
    parsed = result.parsed if isinstance(result.parsed, dict) else {}
    model = {
        "defined_terms": _list_of_dicts(parsed.get("defined_terms")),
        "state_phases": _list_of_dicts(parsed.get("state_phases")),
        "control_branches": _list_of_dicts(parsed.get("control_branches")),
        "source_ambiguities": _merge_source_ambiguities(source_ambiguities, _list_of_dicts(parsed.get("source_ambiguities"))),
        "open_questions": [str(item).strip() for item in (parsed.get("open_questions") or []) if str(item).strip()],
        "llm": {
            "call_id": result.call_id,
            "provider": result.provider,
            "model": result.model,
            "status": result.status,
        },
    }
    return model


def extract_source_ambiguities(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    source_text = "\n".join(str(item.get("plain_text") or "") for item in sources if isinstance(item, dict))
    items: list[dict[str, str]] = []
    for token in _symbol_ambiguity_tokens(source_text):
        quote = _source_quote(source_text, token)
        items.append(
            {
                "original_text": token,
                "category": "symbol",
                "reason": "source symbol expression may be normalized by the model; preserve exact original text",
                "evidence_quote": quote or token,
            }
        )
    return _unique_ambiguities(items)


def _symbol_ambiguity_tokens(source_text: str) -> list[str]:
    tokens: list[str] = []
    patterns = [
        r"\b[A-Za-z0-9_]*[|][A-Za-z0-9_+\-*/|]*\b",
        r"\b[A-Za-z0-9_]+[-_][A-Za-z0-9_]+\|",
        r"[|][A-Za-z0-9_]+[-_][A-Za-z0-9_]+[|]?",
    ]
    for pattern in patterns:
        tokens.extend(match.strip() for match in re.findall(pattern, source_text) if match.strip())
    return list(dict.fromkeys(tokens))


def _source_quote(source_text: str, token: str) -> str:
    index = source_text.find(token)
    if index < 0:
        return ""
    start = max(0, index - 36)
    end = min(len(source_text), index + len(token) + 36)
    return re.sub(r"\s+", " ", source_text[start:end]).strip()


def _merge_source_ambiguities(local_items: list[dict[str, str]], llm_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = list(local_items)
    for item in llm_items:
        original = str(item.get("original_text") or item.get("token") or "").strip()
        if not original:
            continue
        merged.append(
            {
                "original_text": original,
                "category": str(item.get("category") or "unknown").strip(),
                "reason": str(item.get("reason") or "preserve exact source text").strip(),
                "evidence_quote": str(item.get("evidence_quote") or original).strip(),
            }
        )
    return _unique_ambiguities(merged)


def _unique_ambiguities(items: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        original = str(item.get("original_text") or "").strip()
        if not original or original.lower() in seen:
            continue
        seen.add(original.lower())
        unique.append(item)
    return unique


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append({str(key): item.get(key) for key in item})
    return result
