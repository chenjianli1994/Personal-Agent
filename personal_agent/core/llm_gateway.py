from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import connect
from .utils import json_dumps, utc_now


class PersonalLLMError(RuntimeError):
    def __init__(self, code: str, message: str, recoverable: bool = True):
        super().__init__(message)
        self.code = code
        self.recoverable = recoverable


@dataclass(frozen=True)
class LLMResult:
    call_id: int
    provider: str
    model: str
    status: str
    parsed: dict[str, Any]
    raw_text: str


class PersonalLLMGateway:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def status(self) -> dict[str, Any]:
        last_call = self._last_call_status()
        try:
            provider = self._select_provider()
            configured = True
            error = ""
        except PersonalLLMError as exc:
            provider = {"name": "", "model": ""}
            configured = False
            error = str(exc)
        return {
            "configured": configured,
            "provider": provider["name"],
            "model": provider["model"],
            "error": error,
            "configured_source": self._provider_config_source(provider["name"]),
            "last_health_check_at": utc_now(),
            "last_call": last_call,
            "last_call_id": last_call.get("id"),
            "last_call_provider": last_call.get("provider", ""),
            "last_call_model": last_call.get("model", ""),
            "last_call_status": last_call.get("status", ""),
            "last_call_purpose": last_call.get("purpose", ""),
            "last_call_at": last_call.get("created_at", ""),
        }

    def complete_json(
        self,
        *,
        purpose: str,
        system_prompt: str,
        user_prompt: str,
        project_id: int | None = None,
        task_uid: str = "",
    ) -> LLMResult:
        started = time.time()
        provider: dict[str, str] | None = None
        raw_text = ""
        parsed: dict[str, Any] = {}
        status = "ok"
        error = ""
        version_refs = {"prompt_version_id": "", "policy_version_id": "", "contract_version_id": ""}
        try:
            provider = self._select_provider(purpose=purpose)
            if provider["name"] == "fake":
                parsed = self._fake_completion(purpose, user_prompt)
                raw_text = json_dumps(parsed)
            else:
                raw_text = self._chat_completion(provider, system_prompt, user_prompt)
                parsed = self._parse_json(raw_text)
            if not isinstance(parsed, dict):
                raise PersonalLLMError("LLM_INVALID_JSON", "LLM response must be a JSON object")
        except PersonalLLMError as exc:
            status = "failed"
            error = str(exc)
            provider = provider or {"name": "", "model": ""}
            call_id = self._log_call(project_id, task_uid, purpose, provider, status, system_prompt, user_prompt, raw_text, parsed, error, started, version_refs)
            raise PersonalLLMError(exc.code, f"{exc} (llm_call_id={call_id})", exc.recoverable) from exc
        except Exception as exc:
            status = "failed"
            error = str(exc)
            provider = provider or {"name": "", "model": ""}
            call_id = self._log_call(project_id, task_uid, purpose, provider, status, system_prompt, user_prompt, raw_text, parsed, error, started, version_refs)
            raise PersonalLLMError("LLM_CALL_FAILED", f"{exc} (llm_call_id={call_id})") from exc

        call_id = self._log_call(project_id, task_uid, purpose, provider, status, system_prompt, user_prompt, raw_text, parsed, error, started, version_refs)
        parsed["_llm_call_id"] = call_id
        parsed["_llm_provider"] = provider["name"]
        parsed["_llm_model"] = provider["model"]
        parsed["_llm_status"] = status
        parsed["_prompt_version_id"] = ""
        parsed["_policy_version_id"] = ""
        parsed["_contract_version_id"] = ""
        return LLMResult(call_id=call_id, provider=provider["name"], model=provider["model"], status=status, parsed=parsed, raw_text=raw_text)

    def _select_provider(self, purpose: str = "") -> dict[str, str]:
        provider_name = os.environ.get("PERSONAL_AGENT_LLM_PROVIDER", "").lower()
        if provider_name == "fake":
            if os.environ.get("PERSONAL_AGENT_ENABLE_FAKE_LLM") != "1":
                raise PersonalLLMError("LLM_NOT_CONFIGURED", "Fake LLM provider is disabled. Configure DeepSeek with DEEPSEEK_API_KEY.")
            return {"name": "fake", "model": "personal-fake-semantic-fixture", "api_key": "", "base_url": ""}
        model = self._select_model_for_purpose(purpose)
        if not provider_name:
            provider_name = "deepseek"
        if provider_name == "deepseek":
            if not os.environ.get("DEEPSEEK_API_KEY"):
                raise PersonalLLMError("LLM_NOT_CONFIGURED", "DeepSeek is the default LLM provider and requires DEEPSEEK_API_KEY.")
            return {
                "name": "deepseek",
                "api_key": os.environ["DEEPSEEK_API_KEY"],
                "base_url": "https://api.deepseek.com/chat/completions",
                "model": model or "deepseek-v4-flash",
            }
        if provider_name == "dashscope":
            if not os.environ.get("DASHSCOPE_API_KEY"):
                raise PersonalLLMError("LLM_NOT_CONFIGURED", "PERSONAL_AGENT_LLM_PROVIDER=dashscope requires DASHSCOPE_API_KEY.")
            return {
                "name": "dashscope",
                "api_key": os.environ["DASHSCOPE_API_KEY"],
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                "model": model or "qwen3-coder-plus",
            }
        if provider_name == "mimo":
            if not os.environ.get("MIMO_API_KEY"):
                raise PersonalLLMError("LLM_NOT_CONFIGURED", "PERSONAL_AGENT_LLM_PROVIDER=mimo requires MIMO_API_KEY.")
            return {
                "name": "mimo",
                "api_key": os.environ["MIMO_API_KEY"],
                "base_url": "https://token-plan-cn.xiaomimimo.com/v1/chat/completions",
                "model": model or "mimo-v2.5-pro",
            }
        raise PersonalLLMError("LLM_NOT_CONFIGURED", "Unsupported or unconfigured LLM provider. DeepSeek is the default; set DEEPSEEK_API_KEY.")

    def _select_model_for_purpose(self, purpose: str) -> str:
        default_model = os.environ.get("PERSONAL_AGENT_LLM_MODEL", "")
        if self._purpose_tier(purpose) != "fast":
            return default_model
        return os.environ.get("PERSONAL_AGENT_LLM_MODEL_FAST", "") or default_model

    def _purpose_tier(self, purpose: str) -> str:
        if purpose in {"personal_intent_route", "personal_skill_reflect", "personal_learning_reflect"}:
            return "fast"
        return "default"

    def _provider_config_source(self, provider_name: str) -> str:
        explicit = os.environ.get("PERSONAL_AGENT_LLM_PROVIDER", "").strip()
        if explicit:
            return "env:PERSONAL_AGENT_LLM_PROVIDER"
        provider_key = {
            "deepseek": "DEEPSEEK_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
            "mimo": "MIMO_API_KEY",
        }.get(provider_name, "")
        if provider_key and os.environ.get(provider_key):
            return f"env:{provider_key}"
        if os.environ.get("PERSONAL_AGENT_LLM_MODEL"):
            return "env:PERSONAL_AGENT_LLM_MODEL"
        return "not_configured" if not provider_name else "runtime_default"

    def _last_call_status(self) -> dict[str, Any]:
        try:
            with connect(self.db_path) as conn:
                row = conn.execute(
                    """
                    SELECT id, purpose, provider, model, status, error, duration_s, created_at
                    FROM llm_call_logs
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
        except Exception:
            row = None
        return dict(row) if row else {}

    def _chat_completion(self, provider: dict[str, str], system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        def build_request() -> urllib.request.Request:
            return urllib.request.Request(
                provider["base_url"],
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {provider['api_key']}", "Content-Type": "application/json"},
                method="POST",
            )

        first_context = _weak_tls_compatible_context() if provider.get("name") == "dashscope" else None
        body = self._request_with_retries(build_request, first_context, timeout=80)
        return str(body["choices"][0]["message"]["content"])

    def _request_with_retries(
        self,
        build_request: Any,
        context: ssl.SSLContext | None,
        timeout: int,
    ) -> dict[str, Any]:
        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                return self._urlopen_json(build_request(), timeout=timeout, context=context)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise PersonalLLMError("LLM_HTTP_ERROR", f"LLM HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                if _is_weak_tls_certificate_error(exc):
                    context = _weak_tls_compatible_context()
                last_error = exc
            except (http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionResetError, TimeoutError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
        raise PersonalLLMError("LLM_NETWORK_ERROR", str(last_error or "LLM network request failed")) from last_error

    def _urlopen_json(self, request: urllib.request.Request, timeout: int, context: ssl.SSLContext | None = None) -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return json.loads(response.read().decode("utf-8"))

    def _parse_json(self, content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            repaired = _repair_common_json_punctuation(text)
            if repaired != text:
                return json.loads(repaired)
            raise

    def _log_call(
        self,
        project_id: int | None,
        task_uid: str,
        purpose: str,
        provider: dict[str, str],
        status: str,
        system_prompt: str,
        user_prompt: str,
        raw_text: str,
        parsed: dict[str, Any],
        error: str,
        started: float,
        version_refs: dict[str, str],
    ) -> int:
        now = utc_now()
        prompt_excerpt = (system_prompt + "\n\n" + user_prompt)[:6000]
        response_text = raw_text[:12000]
        parsed_text = json_dumps(parsed)[:12000]
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO llm_call_logs(
                    project_id, task_uid, purpose, provider, model, status,
                    prompt_excerpt, response_text, parsed_json, error, duration_s, created_at,
                    prompt_version_id, policy_version_id, contract_version_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    task_uid,
                    purpose,
                    provider.get("name", ""),
                    provider.get("model", ""),
                    status,
                    prompt_excerpt,
                    response_text,
                    parsed_text,
                    error,
                    round(time.time() - started, 3),
                    now,
                    version_refs.get("prompt_version_id", ""),
                    version_refs.get("policy_version_id", ""),
                    version_refs.get("contract_version_id", ""),
                ),
            )
            return int(cur.lastrowid)

    def _fake_completion(self, purpose: str, user_prompt: str) -> dict[str, Any]:
        from .fake_llm_provider import fake_completion

        return fake_completion(purpose, user_prompt)


def _is_weak_tls_certificate_error(exc: BaseException) -> bool:
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text and "EE certificate key too weak" in text


def _weak_tls_compatible_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    return context


def _repair_common_json_punctuation(text: str) -> str:
    repaired = re.sub(r'(?<=")\s*;\s*(?="\w+"\s*:)', ",\n", text)
    return re.sub(r'\\(?!["\\/bfnrtu])', "", repaired)
