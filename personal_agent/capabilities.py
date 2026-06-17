from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CAPABILITY_FLAGS: dict[str, bool] = {
    "chat": True,
    "llm_config": True,
    "session_management": True,
    "codebase": True,
    "patch_candidate": True,
    "patch_apply": True,
    "validation": True,
    "input_sources": True,
    "artifact_drafts": True,
    "artifact_generation": True,
    "knowledge_learning": True,
    "artifact_export": True,
    "skills": True,
}

CAPABILITY_DESCRIPTIONS: dict[str, str] = {
    "chat": "Personal chat turn entrypoint.",
    "llm_config": "Local LLM provider configuration read/write.",
    "session_management": "List, rename, delete, and inspect personal sessions.",
    "codebase": "Read-only local codebase configuration, indexing, search, symbols, and impact analysis.",
    "patch_candidate": "Candidate patch proposal and validation without writing project files.",
    "patch_apply": "Confirmed patch application through the existing controlled patch tool.",
    "validation": "Confirmed build/test/static-analysis commands through the existing allowlist toolchain.",
    "input_sources": "Personal input source ingestion for text and supported document files.",
    "artifact_drafts": "Personal artifact draft and manual revision management.",
    "artifact_generation": "Document artifact draft generation and feedback revision.",
    "knowledge_learning": "Personal knowledge import/search and governed learning candidates.",
    "artifact_export": "Personal draft export and download.",
    "skills": "Project-local document skills registry, SKILL.md inspection, versions, and evaluations.",
}

CAPABILITY_GROUPS: dict[str, list[str]] = {
    "conversation": ["chat", "llm_config", "session_management"],
    "code_understanding": ["codebase"],
    "controlled_patch": ["patch_candidate", "patch_apply"],
    "controlled_validation": ["validation"],
    "input_ingestion": ["input_sources"],
    "artifact_drafting": ["artifact_drafts"],
    "artifact_generation": ["artifact_generation"],
    "knowledge_learning": ["knowledge_learning"],
    "artifact_export": ["artifact_export"],
    "skills": ["skills"],
}

_ENV_PREFIX = "PERSONAL_AGENT_ENABLE_"


@dataclass(frozen=True)
class PersonalAgentCapabilities:
    flags: dict[str, bool]
    config_path: Path
    configured: bool = False

    def enabled(self, name: str) -> bool:
        return bool(self.flags.get(name, False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "flags": dict(self.flags),
            "groups": {
                group: {name: self.enabled(name) for name in names}
                for group, names in CAPABILITY_GROUPS.items()
            },
            "descriptions": dict(CAPABILITY_DESCRIPTIONS),
            "config_path": str(self.config_path),
            "configured": self.configured,
        }


def load_personal_capabilities(workspace: Path) -> PersonalAgentCapabilities:
    workspace = workspace.expanduser().resolve()
    config_path = _capability_config_path(workspace)
    flags = dict(DEFAULT_CAPABILITY_FLAGS)
    configured = False
    if config_path.exists():
        configured = True
        _merge_config_file(flags, config_path)
    _merge_env_overrides(flags)
    return PersonalAgentCapabilities(flags=flags, config_path=config_path, configured=configured)


def _capability_config_path(workspace: Path) -> Path:
    configured = os.getenv("PERSONAL_AGENT_CAPABILITIES_PATH", "").strip().strip('"')
    if configured:
        return Path(configured).expanduser().resolve()
    return workspace / ".personal_agent" / "capabilities.json"


def _merge_config_file(flags: dict[str, bool], config_path: Path) -> None:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid personal capabilities config: {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid personal capabilities config: {config_path}: root must be an object")
    raw_flags = payload.get("capabilities", payload)
    if not isinstance(raw_flags, dict):
        raise ValueError(f"invalid personal capabilities config: {config_path}: capabilities must be an object")
    for key, value in raw_flags.items():
        if key in flags:
            flags[key] = _coerce_bool(value, key)


def _merge_env_overrides(flags: dict[str, bool]) -> None:
    for name in list(flags):
        env_value = os.getenv(f"{_ENV_PREFIX}{name.upper()}")
        if env_value is not None:
            flags[name] = _coerce_bool(env_value, name)


def _coerce_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    raise ValueError(f"capability {key} must be a boolean")
