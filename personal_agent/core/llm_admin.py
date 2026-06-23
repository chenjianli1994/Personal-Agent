from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_gateway import PersonalLLMGateway


LLM_ENV_KEYS = [
    "PERSONAL_AGENT_LLM_PROVIDER",
    "PERSONAL_AGENT_LLM_MODEL",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MIMO_API_KEY",
]

RETIRED_LLM_ENV_KEYS = {
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
}

PROVIDER_KEY_NAMES = {
    "deepseek": "DEEPSEEK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "mimo": "MIMO_API_KEY",
}

DEFAULT_MODELS = {
    "deepseek": "deepseek-v4-flash",
    "dashscope": "qwen3-coder-plus",
    "mimo": "mimo-v2.5-pro",
}

RESTART_EXIT_CODE = 42
BACKEND_STARTED_AT = datetime.now(UTC).isoformat()
PROVIDER_MODELS_URLS = {
    "deepseek": "https://api.deepseek.com/models",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
    "mimo": "https://token-plan-cn.xiaomimimo.com/v1/models",
}
MODEL_OPTIONS_CACHE_TTL_S = 600
_MODEL_OPTIONS_CACHE: dict[str, tuple[float, list[str]]] = {}


def read_personal_llm_admin_config(db_path: Path, env_path: Path | None = None) -> dict[str, Any]:
    env_path = env_path or _default_env_path()
    file_env = _read_env_file(env_path)
    provider = _env_value(file_env, "PERSONAL_AGENT_LLM_PROVIDER") or _infer_provider(file_env)
    if provider not in PROVIDER_KEY_NAMES:
        provider = _infer_provider(file_env)
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
            _provider_option("deepseek", "DeepSeek 官方", file_env),
            _provider_option("dashscope", "DashScope", file_env),
            _provider_option("mimo", "Mimo", file_env),
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
    for key in RETIRED_LLM_ENV_KEYS:
        os.environ.pop(key, None)
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


def _provider_option(provider: str, label: str, env: dict[str, str]) -> dict[str, Any]:
    key_name = PROVIDER_KEY_NAMES.get(provider, "")
    api_key = _env_value(env, key_name) if key_name else ""
    default_model = DEFAULT_MODELS[provider]
    return {
        "value": provider,
        "label": label,
        "default_model": default_model,
        "model_options": _model_options(provider, api_key, default_model),
    }


def _model_options(provider: str, api_key: str, default_model: str) -> list[str]:
    if not api_key:
        return [default_model]
    options = _fetch_provider_model_ids(provider, api_key)
    if default_model not in options:
        options.insert(0, default_model)
    return options


def _fetch_provider_model_ids(provider: str, api_key: str) -> list[str]:
    models_url = PROVIDER_MODELS_URLS.get(provider, "")
    if not models_url:
        return []
    cache_key = f"{provider}:{api_key[:8]}:{api_key[-8:]}"
    cached = _MODEL_OPTIONS_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < MODEL_OPTIONS_CACHE_TTL_S:
        return list(cached[1])
    try:
        req = urllib.request.Request(models_url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=6) as response:
            body = json.loads(response.read().decode("utf-8"))
        options = [
            str(item.get("id") or "").strip()
            for item in body.get("data", [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        options = []
    _MODEL_OPTIONS_CACHE[cache_key] = (now, options)
    return list(options)


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
    for key in RETIRED_LLM_ENV_KEYS:
        existing.pop(key, None)
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
