"""분석 루프 — next → context → finding/unknown → complete 를 단위마다 반복한다.

등급 규칙:
- 결론 등급 = 인용한 근거 중 가장 높은 등급 (도구 근거: LSP=L1, Joern=L2 / 직접 읽은 위치·구조 지도 간선=L0)
- 단위 등급 = 중요(important/critical) 결론 등급의 최솟값. 중요 결론이 없으면 결론 등급의 최댓값
- 목표 등급은 코드 단위만 L1(설치된 엔진 상한 이내), 설정·자산 단위는 L0
- 중요 결론이 목표 등급에 못 미치면 '검증 공백' 미확인 1건 생성 (info 결론은 공백으로 치지 않음)

완료 게이트:
- 단위의 모든 심볼이 '검토됨'이어야 함 (결론에 포함되거나, 특이사항 없음으로 명시)
- important/critical 결론은 다른 에이전트의 독립 검토(oss_claim_review)를 받아야 리포트에서 확정 처리

임대(lease): 소유 에이전트가 단위에 대해 도구를 부를 때마다 자동 연장된다. 만료돼도 다른 에이전트가
가져가기 전이면 원래 소유자는 계속 쓸 수 있다.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import evidence, ledger, unknowns
from oss_analyzer.core.levels import L0, LEVEL_NAMES
from oss_analyzer.core.mask import mask_lines

SEVERITIES = {"info", "important", "critical"}
NEEDS_REVIEW = {"important", "critical"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unit(conn, unit_id: str) -> dict:
    row = ledger.row_to_dict(conn.execute("SELECT * FROM unit WHERE id = ?", (unit_id,)).fetchone())
    if row is None:
        raise ToolError("not_found", f"단위 없음: {unit_id}")
    return row


def _lease_seconds() -> int:
    from oss_analyzer.config import load_settings

    return load_settings().lease_seconds


def _require_lease(unit: dict, agent: str, conn=None) -> None:
    """소유 확인 + 자동 연장. 만료됐어도 아직 아무도 가져가지 않았으면 원래 소유자를 인정한다."""
    if unit["status"] == "done":
        raise ToolError("invalid_request", f"이미 완료된 단위: {unit['id']}")
    if unit["lease_owner"] != agent:
        raise ToolError("lease_conflict", f"{agent} 가 임대한 단위가 아님 (소유: {unit['lease_owner']})",
                        recovery="oss_unit_next 또는 claim 으로 다시 임대")
    if conn is not None:
        with ledger.tx(conn):
            cur = conn.execute("UPDATE unit SET lease_expires_at = ? WHERE id = ? AND lease_owner = ?",
                               (time.time() + _lease_seconds(), unit["id"], agent))
            if cur.rowcount == 0:
                raise ToolError("lease_conflict", "임대가 다른 에이전트로 넘어감", recovery="claim 으로 다시 임대")


def renew(conn, unit_id: str, agent: str) -> dict:
    unit = _unit(conn, unit_id)
    _require_lease(unit, agent, conn)
    return {"unit_id": unit_id, "lease_expires_in_s": _lease_seconds()}


def next_unit(conn, settings: Settings, agent: str, kind: str | None = None) -> dict:
    """임대 가능한 다음 단위. code → asset → test 순, 경로순. 만료된 임대는 회수한다."""
    now = time.time()
    order = "CASE kind WHEN 'code' THEN 0 WHEN 'asset' THEN 1 ELSE 2 END, title"
    with ledger.tx(conn):
        mine = conn.execute(
            "SELECT id FROM unit WHERE status = 'in_progress' AND lease_owner = ? AND lease_expires_at >= ? ORDER BY " + order + " LIMIT 1",
            (agent, now),
        ).fetchone()
        if mine:
            unit_id = mine["id"]
        else:
            params: list = [now]
            kind_clause = ""
            if kind:
                kind_clause = " AND kind = ?"
                params.append(kind)
            row = conn.execute(
                "SELECT id FROM unit WHERE (status = 'pending' OR (status = 'in_progress' AND lease_expires_at < ?))"
                + kind_clause + " ORDER BY " + order + " LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                remaining = conn.execute("SELECT COUNT(*) FROM unit WHERE status != 'done'").fetchone()[0]
                return {"unit": None, "remaining": remaining,
                        "note": "임대 가능한 단위 없음" + (" (다른 에이전트가 진행 중)" if remaining else " — 전부 완료")}
            unit_id = row["id"]
        conn.execute("UPDATE unit SET status = 'in_progress', lease_owner = ?, lease_expires_at = ? WHERE id = ?",
                     (agent, now + settings.lease_seconds, unit_id))
    unit = _unit(conn, unit_id)
    total = conn.execute("SELECT COUNT(*) FROM symbol WHERE unit_id = ?", (unit_id,)).fetchone()[0]
    reviewed = conn.execute("SELECT COUNT(*) FROM symbol WHERE unit_id = ? AND reviewed = 1", (unit_id,)).fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM unit WHERE status = 'done'").fetchone()[0]
    all_units = conn.execute("SELECT COUNT(*) FROM unit").fetchone()[0]
    return {
        "unit": {k: unit[k] for k in ("id", "kind", "title", "members", "line_count", "target_level", "ceiling_level")},
        "lease_expires_in_s": int(unit["lease_expires_at"] - now),
        "symbols": {"total": total, "reviewed": reviewed},
        "progress": {"done": done, "total": all_units},
    }


def claim_unit(conn, settings: Settings, agent: str, unit_id: str) -> dict:
    """특정 단위를 지정해서 임대 (평가용 선택 분석·재분석). 다른 에이전트가 유효하게 임대 중이면 거부."""
    now = time.time()
    with ledger.tx(conn):
        unit = _unit(conn, unit_id)
        if unit["status"] == "done":
            raise ToolError("invalid_request", f"이미 완료된 단위: {unit_id}")
        if unit["status"] == "in_progress" and unit["lease_owner"] != agent and (unit["lease_expires_at"] or 0) >= now:
            raise ToolError("lease_conflict", f"{unit['lease_owner']} 가 임대 중")
        conn.execute("UPDATE unit SET status = 'in_progress', lease_owner = ?, lease_expires_at = ? WHERE id = ?",
                     (agent, now + settings.lease_seconds, unit_id))
    unit = _unit(conn, unit_id)
    return {"unit": {k: unit[k] for k in ("id", "kind", "title", "members", "line_count", "target_level", "ceiling_level")},
            "lease_expires_in_s": int(unit["lease_expires_at"] - now)}


WEAK_EDGE = 0.5  # 이보다 낮은 신뢰도의 이름 기반 간선은 기본 출력에서 뺀다


def short_name(qualified_name: str, project_id: str) -> str:
    qn = qualified_name.removeprefix(project_id + ".")
    parts = qn.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else qn


def unit_context(conn, settings: Settings, project: dict, unit_id: str, agent: str,
                 offset: int = 0, include_calls: bool = True, all_calls: bool = False) -> dict:
    """단위 코드(줄 번호 포함) + 심볼 목록 + 구조 지도 호출 간선.

    코드가 크면 next_offset 으로 이어 읽는다. offset 은 파일 줄 번호가 아니라 '단위 안의 몇 번째 줄'이며,
    next_line 이 다음 페이지가 시작하는 파일:줄이다. 심볼·호출 간선은 첫 페이지(offset=0)에만 넣는다.
    """
    unit = _unit(conn, unit_id)
    _require_lease(unit, agent, conn)
    budget = settings.context_max_chars
    chunks, used, next_offset = [], 0, None
    flat: list[tuple[str, int, str, bool]] = []
    for m in unit["members"]:
        lines = evidence.read_lines(project, m["path"])
        masked = mask_lines(m["path"], lines)
        for n in range(m["start_line"], min(m["end_line"], len(lines)) + 1):
            flat.append((m["path"], n, *masked[n - 1]))
    current_path = None
    masked_lines = []
    for i in range(offset, len(flat)):
        path, n, text, was_masked = flat[i]
        piece = (f"\n=== {path} ===\n" if path != current_path else "") + f"{n:>6}| {text}\n"
        if used + len(piece) > budget and chunks:
            next_offset = i
            break
        if was_masked:
            masked_lines.append(f"{path}:{n}")
        chunks.append(piece)
        used += len(piece)
        current_path = path

    result = {
        "unit_id": unit_id, "commit": project["commit_sha"], "code": "".join(chunks),
        "next_offset": next_offset,
        "next_line": f"{flat[next_offset][0]}:{flat[next_offset][1]}" if next_offset is not None else None,
        "total_lines": len(flat), "masked_lines": masked_lines,
    }
    if offset == 0:
        rows = conn.execute(
            "SELECT id, qualified_name, label, path, start_line, end_line, reviewed FROM symbol WHERE unit_id = ? ORDER BY path, start_line",
            (unit_id,)).fetchall()
        result["symbols"] = [{**dict(r), "name": short_name(r["qualified_name"], project["id"])} for r in rows]
        if include_calls:
            edges = _unit_edges(conn, unit)
            strong = [e for e in edges if all_calls or (e.get("confidence") or 0) >= WEAK_EDGE]
            result["calls"] = _record_call_edges(conn, project, strong)
            result["calls_hidden_weak"] = len(edges) - len(strong)
            result["calls_note"] = ("구조 지도 간선은 이름 기반이라 등급 L0 (strategy 이름에 lsp 가 붙어도 마찬가지). "
                                    "호출 관계를 L1 로 쓰려면 lsp 명령 결과를 인용")
    return result


def _unit_edges(conn, unit: dict, limit: int = 400) -> list[dict]:
    """단위 범위에서 나가거나 들어오는 호출 간선 (인벤토리 때 저장한 구조 지도 캐시)."""
    out: list[dict] = []
    for m in unit["members"]:
        rows = conn.execute(
            "SELECT * FROM call_edge WHERE (caller_path = ? AND COALESCE(call_line, caller_line) BETWEEN ? AND ?) "
            "OR (callee_path = ? AND callee_line BETWEEN ? AND ?) LIMIT ?",
            (m["path"], m["start_line"], m["end_line"], m["path"], m["start_line"], m["end_line"], limit),
        ).fetchall()
        out.extend(dict(r) for r in rows)
    return out[:limit]


def _record_call_edges(conn, project: dict, edges: list[dict]) -> list[dict]:
    """구조 지도 호출 간선을 근거(L0)로 남기고 근거 id 를 붙여 돌려준다.

    위치 검증(파일 읽기)은 트랜잭션 밖에서 먼저 끝내고, 쓰기만 짧은 트랜잭션으로 묶는다.
    """
    known = {r["path"] for r in conn.execute("SELECT path FROM file WHERE category IN ('code','test','asset')").fetchall()}
    pid = project["id"]
    prepared = []
    for e in edges:
        line = e.get("call_line") or e["caller_line"]
        item = {
            "caller": short_name(e["caller"], pid), "callee": short_name(e["callee"], pid),
            "caller_path": e["caller_path"], "call_line": line, "callee_path": e.get("callee_path"),
            "callee_line": e.get("callee_line"), "confidence": e.get("confidence"), "strategy": e.get("strategy"),
            "call_line_is_declaration": not e.get("call_line"),
        }
        loc = None
        if e["caller_path"] in known and line:
            try:
                loc = evidence.validate_location(conn, project, {"path": e["caller_path"], "start_line": line, "end_line": line})
            except ToolError:
                loc = None
        prepared.append((e, item, loc))
    with ledger.tx(conn):
        for e, item, loc in prepared:
            item["evidence_id"] = None
            if loc is not None:
                ev = evidence.record(
                    conn, project, provider="cbm", operation="calls", parameters={"caller": e["caller"]},
                    result={"callee": e["callee"], "strategy": e["strategy"]}, locations=[loc],
                    confidence="derived" if (e.get("confidence") or 0) >= 0.9 else "inferred",
                    limitations=[f"이름 기반 해석(strategy={e['strategy']}, confidence={e['confidence']})"], validated=True,
                )
                item["evidence_id"] = ev["evidence_id"]
    return [item for _, item, _ in prepared]


def record_finding(conn, project: dict, *, unit_id: str, agent: str, text: str, severity: str,
                   evidence_items: list[dict], symbol_ids: list[str], replaces: str | None = None) -> dict:
    """결론 기록. evidence_items: [{"evidence_id": ...}] 또는 [{"path", "start_line", "end_line", "snippet_hash"?}]

    replaces: 문장을 고쳐 다시 내는 경우 옛 결론 id — 새 결론 기록 후 옛 것을 철회한다 (중복 방지).
    """
    unit = _unit(conn, unit_id)
    _require_lease(unit, agent, conn)
    if severity not in SEVERITIES:
        raise ToolError("invalid_request", f"severity 는 {sorted(SEVERITIES)} 중 하나")
    if not evidence_items:
        raise ToolError("evidence_rejected", "근거 없는 결론은 기록할 수 없음")
    valid = {r["id"] for r in conn.execute("SELECT id FROM symbol WHERE unit_id = ?", (unit_id,)).fetchall()}
    foreign = [s for s in symbol_ids if s not in valid]
    if foreign:
        raise ToolError("invalid_request", f"이 단위에 속하지 않은 심볼: {foreign[:5]}")
    # 검증(파일 읽기)은 트랜잭션 밖에서
    cited, reads = [], []
    for item in evidence_items:
        if "evidence_id" in item:
            cited.append(evidence.get(conn, item["evidence_id"]))
        else:
            reads.append((evidence.validate_location(conn, project, item), item.get("note")))
    with ledger.tx(conn):
        ev_ids = [ev["id"] for ev in cited]
        levels = [ev["level"] for ev in cited]
        for loc, note in reads:
            ev = evidence.record(conn, project, provider="agent-read", operation="read",
                                 parameters={"unit_id": unit_id}, result={"note": note},
                                 locations=[loc], confidence="observed", validated=True)
            ev_ids.append(ev["evidence_id"])
            levels.append(ev["level"])
        level = max(levels)
        claim_id = "c_" + hashlib.sha256(f"{unit_id}|{text}".encode()).hexdigest()[:16]
        conn.execute(
            "INSERT OR REPLACE INTO claim (id, unit_id, text, severity, evidence_ids, symbol_ids, level, author, review_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (claim_id, unit_id, text, severity, ledger.dumps(ev_ids), ledger.dumps(symbol_ids), level, agent,
             "pending" if severity in NEEDS_REVIEW else "not_required", _now()),
        )
        conn.executemany("UPDATE symbol SET reviewed = 1, reviewed_by = ? WHERE id = ?", [(claim_id, s) for s in symbol_ids])
    if replaces and replaces != claim_id:
        withdraw_claim(conn, claim_id=replaces, agent=agent, reason=f"{claim_id} 로 교체")
    return {"claim_id": claim_id, "level": LEVEL_NAMES[level], "evidence_ids": ev_ids,
            "review_required": severity in NEEDS_REVIEW,
            "note": "같은 단위에 같은 문장을 다시 내면 이 결론을 덮어씀 (근거 보강용)"}


def mark_symbols(conn, *, unit_id: str, agent: str, symbol_ids: list[str], note: str) -> dict:
    """특이사항 없는 심볼을 검토 완료로 표시 (단순 getter·DTO 등). 사유 필수."""
    unit = _unit(conn, unit_id)
    _require_lease(unit, agent, conn)
    if not note.strip():
        raise ToolError("invalid_request", "검토 사유(note)가 필요함")
    with ledger.tx(conn):
        valid = {r["id"] for r in conn.execute("SELECT id FROM symbol WHERE unit_id = ?", (unit_id,)).fetchall()}
        foreign = [s for s in symbol_ids if s not in valid]
        if foreign:
            raise ToolError("invalid_request", f"이 단위에 속하지 않은 심볼: {foreign[:5]}")
        conn.executemany("UPDATE symbol SET reviewed = 1, reviewed_by = ? WHERE id = ?",
                         [(f"note:{agent}:{note[:200]}", s) for s in symbol_ids])
    return {"marked": len(symbol_ids)}


def withdraw_claim(conn, *, claim_id: str, agent: str, reason: str) -> dict:
    """작성자가 자기 결론을 철회 (문장을 고쳐 다시 낸 경우의 옛 판본 정리 등). 완료된 단위여도 가능, 단위 등급 재계산."""
    row = ledger.row_to_dict(conn.execute("SELECT * FROM claim WHERE id = ?", (claim_id,)).fetchone())
    if row is None:
        raise ToolError("not_found", f"결론 없음: {claim_id}")
    if row["author"] != agent:
        raise ToolError("invalid_request", f"작성자({row['author']})만 철회할 수 있음")
    if not reason.strip():
        raise ToolError("invalid_request", "철회 사유가 필요함")
    with ledger.tx(conn):
        conn.execute("UPDATE claim SET review_status = 'withdrawn', review_note = ? WHERE id = ?", (f"철회: {reason}", claim_id))
        unit = _unit(conn, row["unit_id"])
        if unit["status"] == "done":
            claims = conn.execute("SELECT level, severity FROM claim WHERE unit_id = ? AND review_status != 'withdrawn'",
                                  (row["unit_id"],)).fetchall()
            conn.execute("UPDATE unit SET level = ? WHERE id = ?", (unit_level(claims), row["unit_id"]))
    return {"claim_id": claim_id, "review_status": "withdrawn"}


def unit_level(claims: list) -> int:
    """중요 결론의 최솟값, 중요 결론이 없으면 결론 등급의 최댓값 (모듈 docstring 의 등급 규칙)."""
    important = [c["level"] for c in claims if c["severity"] in NEEDS_REVIEW]
    if important:
        return min(important)
    return max((c["level"] for c in claims), default=L0)


def complete_unit(conn, *, unit_id: str, agent: str, summary: str) -> dict:
    unit = _unit(conn, unit_id)
    _require_lease(unit, agent, conn)
    missing = ledger.rows_to_dicts(conn.execute(
        "SELECT id, qualified_name, start_line FROM symbol WHERE unit_id = ? AND reviewed = 0 ORDER BY start_line", (unit_id,)
    ).fetchall())
    if missing:
        raise ToolError("gate_failed", f"검토 안 된 심볼 {len(missing)}개",
                        recovery="finding 의 symbol_ids 에 포함하거나 mark 로 특이사항 없음 표시",
                        data={"missing": missing[:50]})
    claims = conn.execute("SELECT id, level, severity, text FROM claim WHERE unit_id = ? AND review_status != 'withdrawn'",
                          (unit_id,)).fetchall()
    if not claims and unit["kind"] != "asset":
        raise ToolError("gate_failed", "결론이 하나도 없음 — 단위가 하는 일을 최소 1개 결론으로 남겨야 함")
    level = unit_level(claims)
    weak = [c for c in claims if c["severity"] in NEEDS_REVIEW and c["level"] < unit["target_level"]]
    with ledger.tx(conn):
        conn.execute(
            "UPDATE unit SET status = 'done', level = ?, summary = ?, completed_at = ?, lease_owner = NULL, lease_expires_at = NULL WHERE id = ?",
            (level, summary, _now(), unit_id),
        )
        gap = None
        if weak:
            gap = unknowns.record(
                conn, question=f"단위 {unit['title']} 의 중요 결론 {len(weak)}개가 {LEVEL_NAMES[unit['target_level']]} 근거 없이 "
                               f"직접 읽기(L0)로만 뒷받침됨: " + " / ".join(c["text"][:60] for c in weak[:3]),
                domain="verification", scope=unit_id, severity="low", required_level=unit["target_level"], origin="loop",
                recommended_probes=[{"operation": "lsp", "rationale": "호출 관계는 incoming/outgoing, 값 흐름은 dataflow 로 승격"}],
            )
    return {"unit_id": unit_id, "level": LEVEL_NAMES[level], "verification_gap_unknown": gap,
            "weak_important_claims": [c["id"] for c in weak]}


def review_claim(conn, *, claim_id: str, reviewer: str, verdict: str, note: str) -> dict:
    row = ledger.row_to_dict(conn.execute("SELECT * FROM claim WHERE id = ?", (claim_id,)).fetchone())
    if row is None:
        raise ToolError("not_found", f"결론 없음: {claim_id}")
    if reviewer == row["author"]:
        raise ToolError("invalid_request", "작성자 본인은 검토할 수 없음 — 독립 검토(다른 agent id) 필요")
    if verdict not in {"confirmed", "rejected"}:
        raise ToolError("invalid_request", "verdict 는 confirmed 또는 rejected")
    with ledger.tx(conn):
        conn.execute("UPDATE claim SET review_status = ?, reviewer = ?, review_note = ? WHERE id = ?",
                     (verdict, reviewer, note, claim_id))
        if verdict == "rejected":
            unknowns.record(conn, question=f"결론이 독립 검토에서 반려됨: {row['text'][:200]} — 사유: {note[:200]}",
                            domain="claim", scope=claim_id, severity="high", required_level=row["level"], origin="review")
    return {"claim_id": claim_id, "review_status": verdict}


def classify_files(conn, *, paths: list[str], category: str, reason: str) -> dict:
    from oss_analyzer.core.classify import ANALYZED_CATEGORIES

    allowed = {"doc", "generated", "vendored", "secret", "binary", "asset", "code", "test"}
    if category not in allowed:
        raise ToolError("invalid_request", f"category 는 {sorted(allowed)} 중 하나")
    if not reason.strip():
        raise ToolError("invalid_request", "분류 사유가 필요함")
    note = ""
    if category in ANALYZED_CATEGORIES:
        note = "분석 대상 범주로 바꾼 파일은 인벤토리를 다시 만들어야 단위에 포함됨 (rebuild=true)"
    with ledger.tx(conn):
        updated = 0
        for p in paths:
            cur = conn.execute("UPDATE file SET category = ?, reason = ? WHERE path = ?", (category, f"manual: {reason}", p))
            updated += cur.rowcount
    return {"updated": updated, "note": note}


def list_units(conn, status: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    clause, params = ("WHERE status = ?", [status]) if status else ("", [])
    total = conn.execute(f"SELECT COUNT(*) FROM unit {clause}", params).fetchone()[0]
    rows = ledger.rows_to_dicts(conn.execute(
        f"SELECT id, kind, title, line_count, status, level, target_level, ceiling_level, lease_owner FROM unit {clause} "
        "ORDER BY kind, title LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall())
    return {"total": total, "items": rows, "next_offset": offset + len(rows) if offset + len(rows) < total else None}
