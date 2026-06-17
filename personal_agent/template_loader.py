from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TEMPLATE_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "requirement_analysis_report": [
        "输入摘要",
        "原文事实表",
        "术语与变量定义",
        "需求理解",
        "条件与状态机",
        "歧义与待确认",
        "关键假设",
        "风险与边界",
        "验收建议",
        "证据引用",
    ],
}


@dataclass(frozen=True)
class LoadedTemplate:
    name: str
    path: str
    relative_path: str
    format: str
    hash: str
    content: str
    required_sections: list[str]
    loaded: bool = True

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "relative_path": self.relative_path,
            "format": self.format,
            "hash": self.hash,
            "required_sections": self.required_sections,
            "loaded": self.loaded,
        }


def load_default_template(*, workspace: Path, skill: dict[str, Any]) -> LoadedTemplate:
    frontmatter = skill.get("frontmatter") if isinstance(skill.get("frontmatter"), dict) else {}
    template_info = frontmatter.get("template") if isinstance(frontmatter.get("template"), dict) else {}
    document_type = str(skill.get("document_type") or frontmatter.get("document_type") or "").strip()
    skill_path = Path(str(skill.get("path") or ""))
    skill_dir = skill_path.parent if skill_path else workspace
    relative_path = str(template_info.get("path") or "templates/default.md")
    template_path = (skill_dir / relative_path).resolve()
    content = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
    template_format = str(template_info.get("format") or _format_from_path(template_path)).strip() or "markdown"
    required_sections = frontmatter.get("required_sections")
    return LoadedTemplate(
        name=str(template_info.get("name") or "default"),
        path=str(template_path),
        relative_path=relative_path,
        format=template_format,
        hash=hash_template(content),
        content=content,
        required_sections=_required_sections(document_type, required_sections, content, template_format),
        loaded=bool(content),
    )


def hash_template(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_required_sections(content: str, template_format: str = "markdown") -> list[str]:
    if template_format == "json_table":
        return ["columns", "rows"]
    sections: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            title = title.replace("{{", "").replace("}}", "").strip()
            if title and not title.startswith("可选"):
                sections.append(title)
    return list(dict.fromkeys(sections))


def _required_sections(document_type: str, frontmatter_sections: Any, content: str, template_format: str) -> list[str]:
    if isinstance(frontmatter_sections, list):
        sections = [str(item).strip() for item in frontmatter_sections if str(item).strip()]
        if sections:
            return sections
    defaults = DEFAULT_TEMPLATE_REQUIRED_SECTIONS.get(document_type)
    if defaults:
        return defaults
    return parse_required_sections(content, template_format)


def _format_from_path(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "json_table"
    return "markdown"
