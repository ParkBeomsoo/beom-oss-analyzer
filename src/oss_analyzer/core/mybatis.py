"""MyBatis 매퍼 XML — SQL 구문을 심볼로 뽑고 Java 매퍼 인터페이스 메서드와 연결한다.

구조 지도는 XML 요소를 태그 이름(select, if, foreach…)으로만 잡아 같은 이름끼리 뭉치고 일부 구문은 빠진다.
그래서 <select|insert|update|delete id="..."> 를 직접 찾아 구문마다 심볼(SqlStatement)을 만들고,
namespace 의 클래스 이름 + id 로 Java 메서드를 찾아 호출 간선(strategy=mybatis_namespace)을 붙인다.
"""
from __future__ import annotations

import re

NAMESPACE_RE = re.compile(r"<mapper\b[^>]*\bnamespace\s*=\s*[\"']([\w.$]+)[\"']")
STATEMENT_RE = re.compile(r"<(select|insert|update|delete|sql)\b[^>]*?\bid\s*=\s*[\"']([\w.$-]+)[\"']", re.S)


def is_mapper(text: str) -> bool:
    return "<mapper" in text and NAMESPACE_RE.search(text) is not None


def statements(path: str, text: str) -> tuple[str | None, list[dict]]:
    ns_match = NAMESPACE_RE.search(text)
    namespace = ns_match.group(1) if ns_match else None
    out = []
    for m in STATEMENT_RE.finditer(text):
        tag, sid = m.group(1), m.group(2)
        start = text.count("\n", 0, m.start()) + 1
        close = text.find(f"</{tag}>", m.end())
        self_closed = text.rfind("/>", m.start(), text.find(">", m.start()) + 1) != -1
        end_idx = m.end() if self_closed or close == -1 else close
        end = text.count("\n", 0, end_idx) + 1
        out.append({
            "qualified_name": f"{namespace or path}.{sid}", "name": sid, "label": "SqlStatement" if tag != "sql" else "SqlFragment",
            "path": path, "start_line": start, "end_line": max(end, start), "tag": tag,
        })
    return namespace, out


def link_edges(namespace: str | None, stmts: list[dict], java_methods: list[dict]) -> list[dict]:
    """namespace 클래스의 메서드 ↔ 구문 id. java_methods: [{qualified_name, name, path, start_line}]"""
    if not namespace:
        return []
    cls = namespace.split(".")[-1]
    by_name: dict[str, list[dict]] = {}
    for m in java_methods:
        parts = m["qualified_name"].split(".")
        if len(parts) >= 2 and parts[-2] == cls:
            by_name.setdefault(m["name"], []).append(m)
    edges = []
    for s in stmts:
        if s["label"] != "SqlStatement":
            continue
        for m in by_name.get(s["name"], []):
            edges.append({
                "caller": m["qualified_name"], "caller_path": m["path"], "caller_line": m["start_line"],
                "callee": s["qualified_name"], "callee_path": s["path"], "callee_line": s["start_line"],
                "call_line": None, "confidence": 1.0, "strategy": "mybatis_namespace",
            })
    return edges
