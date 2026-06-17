from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CODE_EXTENSIONS = {
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".hh",
    ".py",
    ".java",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
}
C_FAMILY_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc", ".hh"}
TEST_HINTS = ("test", "tests", "ut_", "_test", "mock", "spec")
SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "out",
    "output",
    "coverage",
    "vendor",
    "third_party",
    "external",
}


@dataclass(frozen=True)
class ScannedCodeFile:
    path: Path
    rel_path: str
    suffix: str
    language: str
    file_type: str
    hash: str
    line_count: int
    last_modified: str
    text: str


@dataclass(frozen=True)
class ParsedSymbol:
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    storage_class: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedInclude:
    source_file: str
    include_text: str
    include_kind: str
    line: int


@dataclass(frozen=True)
class ParsedCallEdge:
    caller_name: str
    callee_name: str
    source_file: str
    line: int
    confidence: float = 0.65
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedConditionalBlock:
    source_file: str
    directive: str
    expression: str
    start_line: int
    end_line: int
    macros: list[str] = field(default_factory=list)
    variant_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedVariableReference:
    source_file: str
    function_name: str
    variable_name: str
    access_type: str
    line: int
    confidence: float = 0.55
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedCodeFile:
    symbols: list[ParsedSymbol]
    includes: list[ParsedInclude]
    call_edges: list[ParsedCallEdge] = field(default_factory=list)
    conditional_blocks: list[ParsedConditionalBlock] = field(default_factory=list)
    variable_references: list[ParsedVariableReference] = field(default_factory=list)
    parser: str = "regex"
    parser_confidence: float = 0.6
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PatchDirective:
    file_path: str
    find: str
    replace: str
    description: str = ""


@dataclass(frozen=True)
class ChangeRequest:
    project_id: int
    requirement_id: str
    change_text: str
    target_symbol: str = ""
    target_file: str = ""
    directives: list[PatchDirective] = field(default_factory=list)


@dataclass(frozen=True)
class PatchPlan:
    plan_uid: str
    target_requirement: str
    modified_files: list[str]
    modified_functions: list[str]
    interface_change: bool
    tests_need_update: bool
    trace_impact: dict[str, Any]
    risk_points: list[str]
    test_suggestions: dict[str, list[str]]
    evidence_refs: dict[str, Any]
