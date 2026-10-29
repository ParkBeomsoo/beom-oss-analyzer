"""분석 루프 도구 — 단위 임대·컨텍스트·결론·완료."""
from __future__ import annotations

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import ledger, loop, project
from oss_analyzer.server import mcp, settings


@mcp.tool()
@enveloped("loop.unit_next")
def oss_unit_next(project_id: str, agent: str = "main", kind: str | None = None) -> dict:
    """다음 분석 단위를 임대한다. 이미 임대한 단위가 있으면 그것을 돌려줌. 병렬 분석 시 agent 를 서로 다르게.

    Args:
        agent: 분석하는 에이전트 id (서브에이전트마다 다르게)
        kind: code / asset / test 중 하나로 제한 (선택)
    """
    conn, _ = project.load(settings, project_id)
    return loop.next_unit(conn, settings, agent, kind)


@mcp.tool()
@enveloped("loop.unit_context")
def oss_unit_context(project_id: str, unit_id: str, agent: str = "main", offset: int = 0,
                     include_calls: bool = True, all_calls: bool = False) -> dict:
    """단위 코드(줄 번호 포함)·심볼 목록·호출 간선(L0 근거 id 포함). 코드가 크면 next_offset 으로 이어 읽기.

    offset 은 '단위 안의 줄 순번'(next_line 이 파일:줄). 심볼·간선은 첫 페이지에만. 신뢰도 0.5 미만 간선은 all_calls=true 일 때만.
    """
    conn, proj = project.load(settings, project_id)
    return loop.unit_context(conn, settings, proj, unit_id, agent, offset, include_calls, all_calls)


@mcp.tool()
@enveloped("loop.unit_renew")
def oss_unit_renew(project_id: str, unit_id: str, agent: str = "main") -> dict:
    """임대 연장 (도구 호출마다 자동 연장되지만, 오래 생각만 할 때 명시적으로)."""
    conn, _ = project.load(settings, project_id)
    return loop.renew(conn, unit_id, agent)


@mcp.tool()
@enveloped("loop.finding_record")
def oss_finding_record(
    project_id: str, unit_id: str, text: str, evidence: list[dict], symbol_ids: list[str],
    severity: str = "info", agent: str = "main", replaces: str | None = None,
) -> dict:
    """결론을 근거와 함께 기록. 근거 위치는 고정 커밋 기준으로 검증되고, 틀리면 거부된다.

    Args:
        text: 결론 문장 (무엇을 어떻게 하는지)
        evidence: [{"evidence_id": "ev_..."}] (도구가 만든 근거) 또는 [{"path", "start_line", "end_line"}] (직접 읽은 위치)
        symbol_ids: 이 결론이 설명하는 심볼 id 들 (oss_unit_context 의 symbols[].id) — 검토 완료로 표시됨
        severity: info / important / critical (important 이상은 독립 검토 필요)
    """
    conn, proj = project.load(settings, project_id)
    return loop.record_finding(conn, proj, unit_id=unit_id, agent=agent, text=text, severity=severity,
                               evidence_items=evidence, symbol_ids=symbol_ids, replaces=replaces)


@mcp.tool()
@enveloped("loop.symbols_mark")
def oss_symbols_mark(project_id: str, unit_id: str, symbol_ids: list[str], note: str, agent: str = "main") -> dict:
    """결론으로 남길 만한 동작이 없는 심볼(단순 getter·DTO 필드 묶음 등)을 사유와 함께 검토 완료 처리."""
    conn, _ = project.load(settings, project_id)
    return loop.mark_symbols(conn, unit_id=unit_id, agent=agent, symbol_ids=symbol_ids, note=note)


@mcp.tool()
@enveloped("loop.unit_complete")
def oss_unit_complete(project_id: str, unit_id: str, summary: str, agent: str = "main") -> dict:
    """단위 완료. 검토 안 된 심볼이 있으면 거부(누락 목록 반환). 목표 등급 미달이면 미확인 자동 생성."""
    conn, _ = project.load(settings, project_id)
    return loop.complete_unit(conn, unit_id=unit_id, agent=agent, summary=summary)


@mcp.tool()
@enveloped("loop.claim_review")
def oss_claim_review(project_id: str, claim_id: str, verdict: str, note: str, reviewer: str) -> dict:
    """important/critical 결론의 독립 검토. 작성자와 다른 reviewer id 여야 함. rejected 면 미확인 생성.

    Args:
        verdict: confirmed / rejected
    """
    conn, _ = project.load(settings, project_id)
    return loop.review_claim(conn, claim_id=claim_id, reviewer=reviewer, verdict=verdict, note=note)


@mcp.tool()
@enveloped("loop.claim_withdraw")
def oss_claim_withdraw(project_id: str, claim_id: str, reason: str, agent: str = "main") -> dict:
    """내가 쓴 결론 철회 (틀렸거나 문장을 고쳐 다시 낸 옛 판본). 완료된 단위도 가능하며 단위 등급을 다시 계산."""
    conn, _ = project.load(settings, project_id)
    return loop.withdraw_claim(conn, claim_id=claim_id, agent=agent, reason=reason)


@mcp.tool()
@enveloped("loop.claims_pending")
def oss_claims_pending(project_id: str, limit: int = 50) -> dict:
    """독립 검토를 기다리는 결론 목록 (critical 먼저)."""
    conn, _ = project.load(settings, project_id)
    rows = ledger.rows_to_dicts(conn.execute(
        "SELECT id, unit_id, text, severity, evidence_ids, level, author FROM claim WHERE review_status = 'pending' "
        "ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, created_at LIMIT ?", (limit,)).fetchall())
    return {"items": rows}
