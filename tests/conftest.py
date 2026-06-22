from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def use_fake_personal_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_ENABLE_FAKE_LLM", "1")
    monkeypatch.setenv("PERSONAL_AGENT_LLM_PROVIDER", "fake")
