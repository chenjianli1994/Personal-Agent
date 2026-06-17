from __future__ import annotations

from pathlib import Path

from personal_agent.content_guard import personal_forbidden_hits


def test_personal_agent_surface_has_no_retired_process_language() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "personal_agent" / "app.py",
        root / "personal_agent" / "routes.py",
        root / "personal_agent" / "runtime.py",
        root / "personal_agent" / "intent_router.py",
        root / "personal_agent" / "artifact_generation.py",
        root / "personal_agent" / "artifact_drafts.py",
        root / "personal_agent" / "skill_registry.py",
        root / "personal_agent" / "core",
        root / "frontend" / "src" / "personal",
        root / "tests" / "test_personal_agent_api.py",
        root / "tests" / "test_personal_intent_router.py",
        root / "tests" / "test_personal_llm_artifact_generation.py",
        root / "tests" / "test_personal_skill_registry.py",
        root / "tests" / "test_personal_artifact_quality.py",
        root / "tests" / "test_personal_learning_knowledge.py",
        root / "tests" / "test_personal_phase5_code_linkage.py",
    ]
    allowed_files = {
        root / "personal_agent" / "content_guard.py",
        Path(__file__).resolve(),
    }

    hits: list[str] = []
    for target in targets:
        paths = [target] if target.is_file() else [path for path in target.rglob("*") if path.is_file()]
        for path in paths:
            if path in allowed_files or path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            for term in personal_forbidden_hits(text):
                hits.append(f"{path.relative_to(root)}: {term}")
    assert hits == []


def test_personal_forbidden_hits_respects_case_and_word_boundaries() -> None:
    text = "aspice baseline ASPICE Gate SYS. SWE. THM-SWE"
    hits = set(personal_forbidden_hits(text))
    assert {"ASPICE", "baseline", "Gate", "SYS.", "SWE.", "THM-SWE"} <= hits

    safe_text = "sys.path sys.executable gateway PersonalLLMGateway quality_gate"
    safe_hits = set(personal_forbidden_hits(safe_text))
    assert "SYS." not in safe_hits
    assert "Gate" not in safe_hits
