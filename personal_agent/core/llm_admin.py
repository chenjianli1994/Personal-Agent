from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_gateway import PersonalLLMGateway


LLM_ENV_KEYS = [
    "PERSONAL_AGENT_LLM_PROVIDER",
    "PERSONAL_AGENT_LLM_MODEL",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
]

PROVIDER_KEY_NAMES = {
    "deepseek": "DEEPSEEK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xai": "XAI_API_KEY",
    "fake": "",
}

DEFAULT_MODELS = {
    "deepseek": "deepseek-v4-flash",
    "dashscope": "qwen3-coder-plus",
    "openrouter": "openai/gpt-4o-mini",
    "xai": "grok-3-mini",
    "fake": "personal-fake-semantic-fixture",
}

RESTART_EXIT_CODE = 42
BACKEND_STARTED_AT = datetime.now(UTC).isoformat()


def read_personal_llm_admin_config(db_path: Path, env_path: Path | None = None) -> dict[str, Any]:
    env_path = env_path or _default_env_path()
    file_env = _read_env_file(env_path)
    provider = _env_value(file_env, "PERSONAL_AGENT_LLM_PROVIDER") or _infer_provider(file_env)
    model = _env_value(file_env, "PERSONAL_AGENT_LLM_MODEL") or DEFAULT_MODELS.get(provider, "")
    status = PersonalLLMGateway(db_path).status()
    api_key_name = PROVIDER_KEY_NAMES.get(provider, "")
    return {
        "provider": provider,
        "model": model,
        "api_key_name": api_key_name,
        "api_key_configured": bool(api_key_name and _env_value(file_env, api_key_name)),
        "env_file": str(env_path.resolve()),
        "available_providers": [
            {"value": "deepseek", "label": "DeepSeek 官方", "default_model": DEFAULT_MODELS["deepseek"]},
            {"value": "dashscope", "label": "DashScope", "default_model": DEFAULT_MODELS["dashscope"]},
            {"value": "openrouter", "label": "OpenRouter", "default_model": DEFAULT_MODELS["openrouter"]},
            {"value": "xai", "label": "xAI", "default_model": DEFAULT_MODELS["xai"]},
            {"value": "fake", "label": "本地测试 Fake", "default_model": DEFAULT_MODELS["fake"]},
        ],
        "status": status,
        "restart_exit_code": RESTART_EXIT_CODE,
        "restart_supported": True,
        "backend": {
            "process_id": os.getpid(),
            "started_at": BACKEND_STARTED_AT,
        },
    }


def save_personal_llm_admin_config(
    *,
    db_path: Path,
    provider: str,
    model: str,
    api_key: str = "",
    env_path: Path | None = None,
    clear_other_provider_keys: bool = False,
) -> dict[str, Any]:
    provider = provider.strip().lower()
    if provider not in PROVIDER_KEY_NAMES:
        raise ValueError(f"unsupported provider: {provider}")
    model = model.strip() or DEFAULT_MODELS.get(provider, "")
    env_path = env_path or _default_env_path()
    env = _read_env_file(env_path)
    env["PERSONAL_AGENT_LLM_PROVIDER"] = provider
    env["PERSONAL_AGENT_LLM_MODEL"] = model
    key_name = PROVIDER_KEY_NAMES.get(provider, "")
    if key_name and api_key.strip():
        env[key_name] = api_key.strip()
    if clear_other_provider_keys:
        for name in PROVIDER_KEY_NAMES.values():
            if name and name != key_name:
                env[name] = ""
    _write_env_file(env_path, env)
    for key in LLM_ENV_KEYS:
        if key in env:
            os.environ[key] = env[key]
    return read_personal_llm_admin_config(db_path, env_path)


def schedule_backend_restart(delay_s: float = 1.0) -> dict[str, Any]:
    timer = threading.Timer(delay_s, lambda: os._exit(RESTART_EXIT_CODE))
    timer.daemon = True
    timer.start()
    return {
        "status": "scheduled",
        "exit_code": RESTART_EXIT_CODE,
        "message": "Backend restart has been scheduled.",
    }


def _default_env_path() -> Path:
    return Path.cwd() / ".env"


def _infer_provider(env: dict[str, str]) -> str:
    for provider, key_name in PROVIDER_KEY_NAMES.items():
        if key_name and _env_value(env, key_name):
            return provider
    return "deepseek"


def _env_value(env: dict[str, str], key: str) -> str:
    return os.environ.get(key) or env.get(key, "")


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"')
    return env


def _write_env_file(path: Path, env: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_env_file(path)
    existing.update(env)
    ordered_keys = [
        "PERSONAL_AGENT_DB",
        "PERSONAL_AGENT_PORT",
        "PERSONAL_AGENT_FRONTEND_PORT",
        *LLM_ENV_KEYS,
    ]
    lines = ["# personal agent local development", ""]
    for key in ordered_keys:
        if key in existing:
            lines.append(f"{key}={existing[key]}")
    for key in sorted(set(existing) - set(ordered_keys)):
        lines.append(f"{key}={existing[key]}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
