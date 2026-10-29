"""엔진 질의 → 근거. 도구가 만든 근거(LSP=L1)는 서버가 직접 기록하므로 에이전트가 위조할 수 없다."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from oss_analyzer.adapters.cbm import CodebaseMemory
from oss_analyzer.adapters.lsp import LspPool
from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import evidence, ledger

MAX_LOCATIONS = 200


def _language(conn, path: str) -> str:
    row = conn.execute("SELECT language, category FROM file WHERE path = ?", (path,)).fetchone()
    if row is None:
        raise ToolError("not_found", f"고정 커밋에 없는 파일: {path}")
    if row["category"] == "secret":
        raise ToolError("evidence_rejected", "비밀 파일은 조회하지 않음")
    if not row["language"]:
        raise ToolError("capability_unavailable", f"{path} 는 언어가 정해지지 않은 파일")
    return row["language"]


def _rel(repo: Path, loc: dict) -> tuple[str, int, int] | None:
    rel = loc.get("relativePath")
    if not rel and loc.get("uri"):
        abs_path = Path(unquote(urlparse(loc["uri"]).path.lstrip("/")))
        try:
            rel = abs_path.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            return None  # 저장소 밖(표준 라이브러리 등)
    if not rel:
        return None
    rng = loc.get("range") or loc.get("selectionRange") or {}
    start = rng.get("start", {}).get("line", 0) + 1
    end = rng.get("end", {}).get("line", start - 1) + 1
    return rel.replace("\\", "/"), start, max(end, start)


def _to_evidence(conn, project: dict, operation: str, params: dict, raw_locs: list[dict], extra: dict) -> dict:
    repo = Path(project["repo_dir"])
    locs, outside, rejected = [], 0, 0
    for loc in raw_locs:
        rel = _rel(repo, loc)
        if rel is None:
            outside += 1
            continue
        path, start, end = rel
        try:
            locs.append(evidence.validate_location(conn, project, {"path": path, "start_line": start, "end_line": end}))
        except ToolError:
            rejected += 1
        if len(locs) >= MAX_LOCATIONS:
            break
    limitations = []
    if outside:
        limitations.append(f"저장소 밖 위치 {outside}개 제외 (외부 경계)")
    if rejected:
        limitations.append(f"검증 실패 위치 {rejected}개 제외 (비밀 파일·범위 오류)")
    if len(raw_locs) > MAX_LOCATIONS:
        limitations.append(f"결과 {len(raw_locs)}개 중 {MAX_LOCATIONS}개만 기록")
    if not locs:
        return {"evidence_id": None, "count": 0, "locations": [], "limitations": limitations}
    with ledger.tx(conn):
        ev = evidence.record(conn, project, provider="lsp", operation=operation, parameters=params,
                             result={"count": len(locs), **extra}, locations=locs, confidence="observed",
                             limitations=limitations, validated=True)
    return {"evidence_id": ev["evidence_id"], "level": "L1", "count": len(locs), "locations": locs, "limitations": limitations}


def lsp_references(conn, pool: LspPool, project: dict, path: str, line: int, column: int) -> dict:
    lang = _language(conn, path)
    server = pool.get(Path(project["repo_dir"]), lang)
    raw = server.references(path, line, column)
    return _to_evidence(conn, project, "references", {"path": path, "line": line, "column": column}, raw, {})


def lsp_definition(conn, pool: LspPool, project: dict, path: str, line: int, column: int) -> dict:
    lang = _language(conn, path)
    raw = pool.get(Path(project["repo_dir"]), lang).definition(path, line, column)
    return _to_evidence(conn, project, "definition", {"path": path, "line": line, "column": column}, raw, {})


def lsp_call_hierarchy(conn, pool: LspPool, project: dict, path: str, line: int, column: int, direction: str) -> dict:
    if direction not in {"incoming", "outgoing"}:
        raise ToolError("invalid_request", "direction 은 incoming 또는 outgoing")
    lang = _language(conn, path)
    calls = pool.get(Path(project["repo_dir"]), lang).call_hierarchy(path, line, column, direction)
    raw_locs, peers = [], []
    for c in calls:
        peer = c["peer"]
        loc = {"uri": peer["uri"], "range": peer.get("selectionRange") or peer["range"]}
        raw_locs.append(loc)
        peers.append({"name": peer.get("name"), "detail": peer.get("detail"), "uri": peer["uri"],
                      "line": (peer.get("selectionRange") or peer["range"])["start"]["line"] + 1})
    out = _to_evidence(conn, project, f"call_hierarchy:{direction}", {"path": path, "line": line, "column": column},
                       raw_locs, {"peers": [p["name"] for p in peers][:MAX_LOCATIONS]})
    out["peers"] = peers[:MAX_LOCATIONS]
    return out


def graph_query(settings: Settings, project: dict, cypher: str, max_rows: int = 200) -> dict:
    lowered = cypher.lower()
    if any(word in lowered for word in ("create ", "delete ", "merge ", "set ", "remove ")):
        raise ToolError("invalid_request", "읽기 전용 질의만 허용")
    rows = CodebaseMemory(settings).query(project["id"], cypher, page_rows=min(max_rows, 5000))
    return {"rows": rows[:max_rows], "total": len(rows), "truncated": len(rows) > max_rows,
            "note": "구조 지도 결과는 이름 기반 해석일 수 있음 — 결론 근거로 쓰려면 LSP 로 확인"}
