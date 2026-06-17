from __future__ import annotations

import importlib.util
import re

from .schemas import (
    C_FAMILY_EXTENSIONS,
    ParsedCallEdge,
    ParsedCodeFile,
    ParsedConditionalBlock,
    ParsedInclude,
    ParsedSymbol,
    ParsedVariableReference,
)


CONTROL_WORDS = {"if", "for", "while", "switch", "return", "sizeof"}
BUILTIN_CALLS = {
    "assert",
    "defined",
    "sizeof",
    "memcpy",
    "memset",
    "printf",
    "snprintf",
}


def parser_capabilities() -> dict[str, object]:
    tree_sitter_available = _module_available("tree_sitter")
    clang_available = _module_available("clang") or _module_available("clang.cindex")
    selected = "regex"
    confidence = 0.62
    limitations = []
    limitations.append("tree-sitter-c adapter is optional and not enabled; regex parser fallback is active")
    if not tree_sitter_available:
        limitations.append("tree-sitter runtime is unavailable")
    limitations.append("libclang adapter is optional and not enabled; compile_commands-aware macro/type expansion is disabled")
    if not clang_available:
        limitations.append("libclang runtime is unavailable")
    return {
        "selected": selected,
        "confidence": confidence,
        "available": {
            "regex": True,
            "tree_sitter_c": tree_sitter_available,
            "libclang": clang_available,
        },
        "fallback": selected == "regex",
        "limitations": limitations,
    }


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def parse_code_file(text: str, rel_path: str, suffix: str) -> ParsedCodeFile:
    capabilities = parser_capabilities()
    parser_name = str(capabilities["selected"])
    confidence = float(capabilities["confidence"])
    limitations = [str(item) for item in capabilities.get("limitations", [])]
    if suffix in C_FAMILY_EXTENSIONS:
        functions = _parse_c_functions(text, rel_path)
        global_variables = _parse_c_global_variables(text, rel_path, functions)
        return ParsedCodeFile(
            symbols=[
                *functions,
                *_parse_c_macros(text, rel_path),
                *_parse_c_typedef_structs(text, rel_path),
                *_parse_c_typedef_enums(text, rel_path),
                *_parse_c_typedefs(text, rel_path),
                *_parse_c_named_types(text, rel_path),
                *global_variables,
            ],
            includes=_parse_c_includes(text, rel_path),
            call_edges=_parse_c_call_edges(text, rel_path, functions),
            conditional_blocks=_parse_c_conditionals(text, rel_path),
            variable_references=_parse_c_variable_references(text, rel_path, functions, global_variables),
            parser=parser_name,
            parser_confidence=confidence,
            limitations=limitations,
        )
    return ParsedCodeFile(symbols=_parse_generic_symbols(text, rel_path, suffix), includes=[], parser="generic-regex", parser_confidence=0.5)


def _parse_c_functions(text: str, rel_path: str) -> list[ParsedSymbol]:
    pattern = re.compile(
        r"(?m)^\s*(?P<prefix>(?:static\s+|extern\s+|inline\s+)*)"
        r"(?P<ret>[A-Za-z_][\w\s\*\(\)]*?)\s+"
        r"(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{}]*)\)\s*(?P<body>\{|;)"
    )
    symbols: list[ParsedSymbol] = []
    for match in pattern.finditer(text):
        name = match.group("name")
        if name in CONTROL_WORDS:
            continue
        start_line = _line_number(text, match.start())
        end_line = _function_end_line(text, match.end() - 1) if match.group("body") == "{" else start_line
        prefix = match.group("prefix") or ""
        storage = "static" if "static" in prefix else ("extern" if "extern" in prefix else "")
        symbols.append(
            ParsedSymbol(
                name=name,
                kind="function",
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                signature=_trim(match.group(0), 240),
                storage_class=storage,
                metadata={"definition": match.group("body") == "{", "return_type": _trim(match.group("ret"), 120)},
            )
        )
    return symbols


def _parse_c_macros(text: str, rel_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    for match in re.finditer(r"(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\b(.*)$", text):
        symbols.append(
            ParsedSymbol(
                name=match.group(1),
                kind="macro",
                file_path=rel_path,
                start_line=_line_number(text, match.start()),
                end_line=_line_number(text, match.end()),
                signature=_trim(match.group(0), 220),
                metadata={"value": _trim(match.group(2).strip(), 180)},
            )
        )
    return symbols


def _parse_c_global_variables(text: str, rel_path: str, functions: list[ParsedSymbol]) -> list[ParsedSymbol]:
    ranges = [(symbol.start_line, symbol.end_line) for symbol in functions if symbol.metadata.get("definition")]
    symbols: list[ParsedSymbol] = []
    pattern = re.compile(
        r"(?m)^\s*(?P<prefix>(?:static\s+|extern\s+|volatile\s+|const\s+)*)"
        r"(?P<type>[A-Za-z_][\w\s\*]*?)\s+"
        r"(?P<name>[A-Za-z_]\w*)\s*(?:=\s*[^;]+)?;"
    )
    for match in pattern.finditer(text):
        line = _line_number(text, match.start())
        if any(start <= line <= end for start, end in ranges):
            continue
        raw = _trim(match.group(0), 220)
        if "(" in raw or ")" in raw or _brace_depth_before(text, match.start()) > 0:
            continue
        name = match.group("name")
        type_text = _trim(match.group("type"), 120)
        if name in CONTROL_WORDS or type_text in {"return", "typedef"} or type_text.startswith("typedef"):
            continue
        prefix = match.group("prefix") or ""
        storage = "static" if "static" in prefix else ("extern" if "extern" in prefix else "")
        symbols.append(
            ParsedSymbol(
                name=name,
                kind="variable",
                file_path=rel_path,
                start_line=line,
                end_line=line,
                signature=raw,
                storage_class=storage,
                metadata={"type": type_text, "global": True, "extern": "extern" in prefix},
            )
        )
    return symbols


def _parse_c_typedef_structs(text: str, rel_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    pattern = re.compile(r"typedef\s+struct(?:\s+([A-Za-z_]\w*))?\s*\{(?P<body>.*?)\}\s*([A-Za-z_]\w*)\s*;", re.DOTALL)
    for match in pattern.finditer(text):
        name = match.group(3)
        fields = re.findall(r"\b([A-Za-z_]\w*(?:\s*\*)?)\s+([A-Za-z_]\w*)\s*(?:\[[^\]]+\])?\s*;", match.group("body"))
        symbols.append(
            ParsedSymbol(
                name=name,
                kind="typedef",
                file_path=rel_path,
                start_line=_line_number(text, match.start()),
                end_line=_line_number(text, match.end()),
                signature=_trim(match.group(0), 260),
                metadata={"type_category": "struct", "tag": match.group(1) or "", "fields": [{"type": _trim(t, 80), "name": n} for t, n in fields]},
            )
        )
    return symbols


def _parse_c_typedef_enums(text: str, rel_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    pattern = re.compile(r"typedef\s+enum(?:\s+([A-Za-z_]\w*))?\s*\{(?P<body>.*?)\}\s*([A-Za-z_]\w*)\s*;", re.DOTALL)
    for match in pattern.finditer(text):
        values = [item.strip().split("=", 1)[0].strip() for item in match.group("body").split(",") if item.strip()]
        symbols.append(
            ParsedSymbol(
                name=match.group(3),
                kind="typedef",
                file_path=rel_path,
                start_line=_line_number(text, match.start()),
                end_line=_line_number(text, match.end()),
                signature=_trim(match.group(0), 260),
                metadata={"type_category": "enum", "tag": match.group(1) or "", "values": values[:80]},
            )
        )
    return symbols


def _parse_c_typedefs(text: str, rel_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    for match in re.finditer(r"(?m)^\s*typedef\s+(?!struct\b|enum\b)(.+?)\s+([A-Za-z_]\w*)\s*;", text):
        symbols.append(
            ParsedSymbol(
                name=match.group(2),
                kind="typedef",
                file_path=rel_path,
                start_line=_line_number(text, match.start()),
                end_line=_line_number(text, match.end()),
                signature=_trim(match.group(0), 220),
                metadata={"type_category": "alias", "base_type": _trim(match.group(1), 120)},
            )
        )
    return symbols


def _parse_c_named_types(text: str, rel_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    for kind in ("struct", "enum"):
        for match in re.finditer(rf"\b{kind}\s+([A-Za-z_]\w*)", text):
            symbols.append(
                ParsedSymbol(
                    name=match.group(1),
                    kind=kind,
                    file_path=rel_path,
                    start_line=_line_number(text, match.start()),
                    end_line=_line_number(text, match.end()),
                    signature=_trim(match.group(0), 120),
                    metadata={"type_category": kind},
                )
            )
    return symbols


def _parse_c_includes(text: str, rel_path: str) -> list[ParsedInclude]:
    includes: list[ParsedInclude] = []
    for match in re.finditer(r"(?m)^\s*#\s*include\s+([<\"])([^>\"]+)[>\"]", text):
        includes.append(
            ParsedInclude(
                source_file=rel_path,
                include_text=match.group(2),
                include_kind="system" if match.group(1) == "<" else "local",
                line=_line_number(text, match.start()),
            )
        )
    return includes


def _parse_c_call_edges(text: str, rel_path: str, functions: list[ParsedSymbol]) -> list[ParsedCallEdge]:
    edges: list[ParsedCallEdge] = []
    definitions = [item for item in functions if item.metadata.get("definition") and item.end_line >= item.start_line]
    for function in definitions:
        body = _line_slice(text, function.start_line, function.end_line)
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", body):
            callee = match.group(1)
            if callee == function.name or callee in CONTROL_WORDS or callee in BUILTIN_CALLS:
                continue
            before = body[max(0, match.start() - 80) : match.start()]
            if re.search(r"\b(?:void|bool|char|int|long|short|float|double|struct|enum|typedef)\s+$", before):
                continue
            edges.append(
                ParsedCallEdge(
                    caller_name=function.name,
                    callee_name=callee,
                    source_file=rel_path,
                    line=function.start_line + body.count("\n", 0, match.start()),
                    metadata={"parser": "regex", "caller_start_line": function.start_line},
                )
            )
    return _dedupe_call_edges(edges)


def _parse_c_conditionals(text: str, rel_path: str) -> list[ParsedConditionalBlock]:
    directive_pattern = re.compile(r"(?m)^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)$")
    stack: list[dict[str, object]] = []
    blocks: list[ParsedConditionalBlock] = []
    for match in directive_pattern.finditer(text):
        directive = match.group(1)
        expression = match.group(2).strip()
        line = _line_number(text, match.start())
        if directive in {"ifdef", "ifndef", "if", "elif", "else"}:
            if directive in {"elif", "else"} and stack:
                previous = stack.pop()
                blocks.append(_conditional_from_stack(rel_path, previous, line - 1))
            stack.append({"directive": directive, "expression": expression, "start_line": line})
        elif directive == "endif" and stack:
            previous = stack.pop()
            blocks.append(_conditional_from_stack(rel_path, previous, line))
    last_line = len(text.splitlines())
    while stack:
        previous = stack.pop()
        blocks.append(_conditional_from_stack(rel_path, previous, last_line))
    return blocks


def _parse_c_variable_references(
    text: str,
    rel_path: str,
    functions: list[ParsedSymbol],
    global_variables: list[ParsedSymbol],
) -> list[ParsedVariableReference]:
    variables = [item.name for item in global_variables]
    if not variables:
        return []
    refs: list[ParsedVariableReference] = []
    definitions = [item for item in functions if item.metadata.get("definition") and item.end_line >= item.start_line]
    for function in definitions:
        body = _line_slice(text, function.start_line, function.end_line)
        for variable in variables:
            pattern = re.compile(rf"\b{re.escape(variable)}\b")
            for match in pattern.finditer(body):
                line = function.start_line + body.count("\n", 0, match.start())
                snippet = _line_at(text, line)
                refs.append(
                    ParsedVariableReference(
                        source_file=rel_path,
                        function_name=function.name,
                        variable_name=variable,
                        access_type=_variable_access_type(snippet, variable),
                        line=line,
                        metadata={"parser": "regex", "snippet": _trim(snippet, 160)},
                    )
                )
    return _dedupe_variable_refs(refs)


def _parse_generic_symbols(text: str, rel_path: str, suffix: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        pattern = r"\b(?:function|class|interface|type|const|let)\s+([A-Za-z_]\w*)"
    else:
        pattern = r"(?m)^\s*(?:def|class)\s+([A-Za-z_]\w*)"
    for match in re.finditer(pattern, text):
        symbols.append(
            ParsedSymbol(
                name=match.group(1),
                kind="symbol",
                file_path=rel_path,
                start_line=_line_number(text, match.start()),
                end_line=_line_number(text, match.end()),
                signature=_trim(match.group(0), 180),
            )
        )
    return symbols[:80]


def _function_end_line(text: str, open_brace_offset: int) -> int:
    depth = 0
    for index in range(open_brace_offset, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth <= 0:
                return _line_number(text, index)
    return _line_number(text, open_brace_offset)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


def _brace_depth_before(text: str, offset: int) -> int:
    depth = 0
    for char in text[: max(offset, 0)]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    return depth


def _line_slice(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[max(0, start_line - 1) : max(start_line - 1, end_line)])


def _line_at(text: str, line_number: int) -> str:
    lines = text.splitlines()
    if line_number <= 0 or line_number > len(lines):
        return ""
    return lines[line_number - 1]


def _conditional_from_stack(rel_path: str, data: dict[str, object], end_line: int) -> ParsedConditionalBlock:
    expression = str(data.get("expression") or "")
    macros = _macros_from_expression(expression)
    return ParsedConditionalBlock(
        source_file=rel_path,
        directive=str(data.get("directive") or ""),
        expression=expression,
        start_line=int(data.get("start_line") or 0),
        end_line=max(end_line, int(data.get("start_line") or 0)),
        macros=macros,
        variant_key=" && ".join(macros) if macros else _trim(expression, 80),
        metadata={"parser": "regex"},
    )


def _macros_from_expression(expression: str) -> list[str]:
    cleaned = re.sub(r"\bdefined\s*\(([^)]+)\)", r"\1", expression)
    cleaned = re.sub(r"\bdefined\s+([A-Za-z_]\w*)", r"\1", cleaned)
    macros = []
    for token in re.findall(r"\b[A-Z_][A-Z0-9_]{2,}\b", cleaned):
        if token not in macros:
            macros.append(token)
    return macros


def _variable_access_type(line: str, variable: str) -> str:
    escaped = re.escape(variable)
    if re.search(rf"\b{escaped}\b\s*(?:=|\+=|-=|\*=|/=|%=|\+\+|--)", line) or re.search(rf"(?:\+\+|--)\s*\b{escaped}\b", line):
        return "write"
    if re.search(rf"&\s*\b{escaped}\b", line):
        return "address"
    return "read"


def _dedupe_call_edges(edges: list[ParsedCallEdge]) -> list[ParsedCallEdge]:
    seen: set[tuple[str, str, int]] = set()
    result: list[ParsedCallEdge] = []
    for edge in edges:
        key = (edge.caller_name, edge.callee_name, edge.line)
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _dedupe_variable_refs(refs: list[ParsedVariableReference]) -> list[ParsedVariableReference]:
    seen: set[tuple[str, str, str, int]] = set()
    result: list[ParsedVariableReference] = []
    for ref in refs:
        key = (ref.function_name, ref.variable_name, ref.access_type, ref.line)
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _trim(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."
