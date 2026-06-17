from __future__ import annotations

from pathlib import Path


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
        root / "frontend" / "src" / "personal",
        root / "tests" / "test_personal_agent_api.py",
        root / "tests" / "test_personal_intent_router.py",
        root / "tests" / "test_personal_llm_artifact_generation.py",
        root / "tests" / "test_personal_skill_registry.py",
        root / "tests" / "test_personal_artifact_quality.py",
    ]
    blocked = [
        "AS" + "PICE",
        "SYS.",
        "SWE.",
        "THM-" + "SWE",
        "Ga" + "te",
        "base" + "line",
        "基" + "线",
        "评审" + "闭环",
        "正式 " + "artifact",
        "trace" + "_matrix",
        "/api/" + "agent/tasks",
        "unified" + "-turn",
        "artifact" + "_type",
        "当前" + "产物",
        "追溯" + "矩阵",
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
            for term in blocked:
                if term in text:
                    hits.append(f"{path.relative_to(root)}: {term}")
    assert hits == []
