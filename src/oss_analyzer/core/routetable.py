"""라우트 표 매크로 해석 — `"경로" => verb(handler)` / `[verb(h), ...]` / `sub(하위표)` 형태의 선언형 라우팅.

예) Meilisearch 의 #[routes::routes(routes("/keys" => sub(api_key::ApiKeyApi), "" => [get(list), post(create)]))]
    pub struct MeilisearchApi;

- 표마다 '주인'(바로 뒤의 struct/fn/mod 이름)을 기록하고, sub(X) 는 X 표를 그 경로 아래에 마운트한다.
- macro_rules! 본문 안의 표에서 `$( $route => [...] ),*` 반복은 그 매크로 호출부의
  `{ route: "/x", attr: y, ... }` 묶음으로 펼친다 (정적 근사 — 가정으로 표시).
- 마운트되지 않은 표는 루트("")로 가정한다 (가정으로 표시).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from oss_analyzer.core.resolve import Unresolved, balanced, literal, normalize_path, split_top, strip_comments

VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "any"}
ARROW_RE = re.compile(r'("(?:[^"\\]|\\.)*"|\$\w+)\s*=>')
OWNER_RE = re.compile(r"\b(?:struct|fn|enum|mod|impl)\s+(\w+)")
MACRO_DEF_RE = re.compile(r"macro_rules!\s*(\w+)")


@dataclass
class Entry:
    path: str                 # 표 안의 경로 조각 ("" 가능)
    kind: str                 # http | mount
    method: str | None = None
    handler: str | None = None
    target: str | None = None  # mount 대상 표 주인
    line: int = 0             # 근거 줄 (파일 기준)
    file: str = ""
    assumptions: list[str] = field(default_factory=list)


@dataclass
class Table:
    owner: str
    file: str
    line: int
    entries: list[Entry]


def _find_table(text: str) -> tuple[str, int] | None:
    """첫 `"..." =>` 를 감싸는 괄호 묶음의 내용과 시작 위치."""
    m = ARROW_RE.search(text)
    if not m:
        return None
    depth, i = 0, m.start()
    while i > 0:
        i -= 1
        ch = text[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                if ch != "(":
                    return None
                inner, _ = balanced(text, i)
                return inner, i
            depth -= 1
    return None


def _strip_strings(text: str) -> str:
    """문자열 리터럴 내용을 지운다 (설명 문구 속 'mod', 'impl' 같은 단어를 주인 이름으로 오인하지 않도록)."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)


def _specs(spec: str) -> list[tuple[str, str]]:
    spec = spec.strip()
    if spec.startswith("["):
        spec = balanced(spec, 0)[0]
    out = []
    for item in split_top(spec):
        m = re.match(r"^\s*(\$?\w+)\s*\((.*)\)\s*$", item, re.S)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def _line_at(text: str, index: int, base_line: int) -> int:
    return base_line + text.count("\n", 0, index)


def parse_table(file: str, text: str, base_line: int, macro_values: list[dict] | None = None,
                following: str = "") -> Table | None:
    """text: 표를 포함한 원문(속성 또는 macro_rules 본문), base_line: text 첫 줄의 파일 줄 번호.

    following: 원문 바로 다음 몇 줄 — 속성의 주인(`pub struct X;`)은 속성 범위 밖 다음 줄에 있다.
    """
    clean = strip_comments(text)
    found = _find_table(clean)
    if not found:
        return None
    inner, start = found
    after = _strip_strings(clean[start + len(inner) + 2:]) + "\n" + _strip_strings(strip_comments(following))
    owner_m = OWNER_RE.search(after)
    owner = owner_m.group(1) if owner_m else f"{file}:{base_line}"
    entries: list[Entry] = []
    for raw in split_top(inner):
        repeated = raw.startswith("$(")
        body = raw[2:].rsplit(")", 1)[0] if repeated else raw
        if "=>" not in body:
            continue
        left, right = body.split("=>", 1)
        left = left.strip()
        line = _line_at(clean, clean.find(raw, start), base_line)
        variants: list[dict] = [{}]
        assumptions: list[str] = []
        if left.startswith("$"):
            if not macro_values:
                entries.append(Entry(left, "unresolved", line=line, file=file,
                                     assumptions=["매크로 반복 경로 — 호출부를 찾지 못함"]))
                continue
            variants = macro_values
            assumptions = ["macro_rules 반복을 호출부 값으로 펼침 (정적 근사)"]
        for values in variants:
            def sub_vars(s: str, values: dict = values) -> str:
                return re.sub(r"\$(\w+)", lambda m: str(values.get(m.group(1), {}).get("value", m.group(0))), s)

            path_expr = sub_vars(left)
            path = literal(path_expr)
            if path is None:
                entries.append(Entry(path_expr, "unresolved", line=line, file=file, assumptions=assumptions))
                continue
            ev_line = values.get(left.lstrip("$"), {}).get("line", line) if values else line
            ev_file = values.get(left.lstrip("$"), {}).get("file", file) if values else file
            for verb, arg in _specs(sub_vars(right)):
                if verb == "sub":
                    entries.append(Entry(path, "mount", target=arg.split("::")[-1].strip(), line=ev_line, file=ev_file,
                                         assumptions=list(assumptions)))
                elif verb.lower() in VERBS:
                    entries.append(Entry(path, "http", method=verb.upper(), handler=arg, line=ev_line, file=ev_file,
                                         assumptions=list(assumptions)))
    return Table(owner, file, base_line, entries)


def macro_invocation_values(sources: dict[str, list[str]], macro: str) -> list[dict]:
    """macro!( {k: v, ...}, {...} ) 호출부에서 묶음별 {k: {value, line, file}} 목록."""
    out: list[dict] = []
    call_re = re.compile(rf"\b{re.escape(macro)}!\s*\(")
    for file, lines in sources.items():
        text = "\n".join(lines)
        for m in call_re.finditer(text):
            if text[max(0, m.start() - 15):m.start()].rstrip().endswith("macro_rules!"):
                continue
            try:
                inner, _ = balanced(text, m.end() - 1)
            except Unresolved:
                continue
            offset = m.end()
            for group in split_top(strip_comments(inner)):
                group = group.strip()
                if not group.startswith("{"):
                    continue
                body = balanced(group, 0)[0]
                values: dict = {}
                for pair in split_top(body):
                    pm = re.match(r"^\s*(\w+)\s*:(?!:)\s*(.*)$", pair, re.S)
                    if not pm:
                        continue
                    key, val = pm.group(1), pm.group(2).strip()
                    idx = text.find(f"{key}: {val.splitlines()[0] if val else ''}", offset)
                    values[key] = {"value": val, "line": text.count("\n", 0, idx) + 1 if idx >= 0 else 0, "file": file}
                if values:
                    out.append(values)
    return out


def enclosing_macro(lines: list[str], line: int) -> str | None:
    for ln in range(line, 0, -1):
        m = MACRO_DEF_RE.search(lines[ln - 1])
        if m:
            return m.group(1)
    return None


def compose(tables: list[Table]) -> list[tuple[Entry, str, list[str]]]:
    """표 → (항목, 전체 경로, 가정) 목록. sub 로 연결된 트리를 루트부터 펼친다."""
    by_owner = {t.owner: t for t in tables}
    mounted = {e.target for t in tables for e in t.entries if e.kind == "mount"}
    roots = [t for t in tables if t.owner not in mounted]
    out: list[tuple[Entry, str, list[str]]] = []

    def walk(table: Table, prefix: str, assumptions: list[str], seen: tuple[str, ...]) -> None:
        if table.owner in seen:
            return
        for e in table.entries:
            full = normalize_path(prefix + ("/" if prefix and e.path and not e.path.startswith("/") else "") + e.path) or "/"
            if e.kind == "http":
                out.append((e, full, assumptions + e.assumptions))
            elif e.kind == "mount" and e.target in by_owner:
                walk(by_owner[e.target], full, assumptions + e.assumptions, seen + (table.owner,))
            elif e.kind == "mount":
                out.append((e, full, assumptions + [f"마운트 대상 표 {e.target} 를 찾지 못함"]))
            else:
                out.append((e, full, assumptions + e.assumptions + ["경로를 해석하지 못함"]))

    for root in roots:
        walk(root, "", [] if len(roots) == 1 else [f"표 {root.owner} 는 마운트되지 않아 루트로 가정"], ())
    return out
