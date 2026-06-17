from __future__ import annotations

from personal_agent.runtime import _safe_fallback_answer


def test_safe_fallback_answer_reports_llm_balance_error_to_user() -> None:
    route = {
        "reason": "LLM intent route failed; safe answer-only fallback.",
        "router_source": "fallback",
        "llm": {
            "call_id": 138,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "status": "failed",
            "purpose": "personal_intent_route",
            "error": (
                'LLM HTTP 402: {"error":{"message":"Insufficient Balance",'
                '"type":"unknown_error","param":null,"code":"invalid_request_error"}} '
                "(llm_call_id=138)"
            ),
        },
    }

    answer = _safe_fallback_answer({"active_source_uids": ["src_1"]}, route)

    assert "LLM 调用失败" in answer
    assert "deepseek/deepseek-v4-flash" in answer
    assert "call_id=138" in answer
    assert "402" in answer
    assert "余额或额度不足" in answer
    assert "本轮不会生成草稿" in answer

