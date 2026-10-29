"""미확인·근거 도구."""
from __future__ import annotations

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import evidence, ledger, project, unknowns
from oss_analyzer.server import mcp, settings


@mcp.tool()
@enveloped("findings.unknown_record")
def oss_unknown_record(
    project_id: str, question: str, domain: str, severity: str = "medium", required_level: str = "L1",
    scope: str | None = None, supporting_evidence_ids: list[str] | None = None,
    recommended_probes: list[dict] | None = None, agent: str = "main",
) -> dict:
    """확정하지 못한 것을 미확인으로 기록. 같은 질문·범위는 중복 생성되지 않음.

    Args:
        domain: 예) dataflow / dynamic-dispatch / config / entrypoint / external-dependency
        severity: low / medium / high / critical
        required_level: 해결에 필요한 최소 근거 등급 (L0~L3)
        recommended_probes: [{"operation": "...", "rationale": "..."}]
    """
    conn, _ = project.load(settings, project_id)
    with ledger.tx(conn):
        return unknowns.record(conn, question=question, domain=domain, scope=scope, severity=severity,
                               required_level=required_level, supporting_evidence_ids=supporting_evidence_ids,
                               recommended_probes=recommended_probes, origin=agent)


@mcp.tool()
@enveloped("findings.unknown_update")
def oss_unknown_update(
    project_id: str, unknown_id: str, expected_revision: int, status: str | None = None,
    add_supporting: list[str] | None = None, add_contradicting: list[str] | None = None,
    resolution: dict | None = None, note: str | None = None, agent: str = "main",
) -> dict:
    """미확인 갱신·해결. 해결(resolution.disposition=verified)은 required_level 이상 근거가 있어야 함.

    Args:
        expected_revision: 마지막으로 읽은 revision (다르면 revision_conflict)
        resolution: {"disposition": "verified"|"withdrawn"|"out-of-scope", "rationale": "...", "evidence_ids": [...]}
    """
    conn, _ = project.load(settings, project_id)
    with ledger.tx(conn):
        return unknowns.update(conn, unknown_id, expected_revision=expected_revision, actor=agent, status=status,
                               add_supporting=add_supporting, add_contradicting=add_contradicting,
                               resolution=resolution, note=note)


@mcp.tool()
@enveloped("findings.unknowns_list")
def oss_unknowns_list(project_id: str, status: str | None = None, severity: str | None = None,
                      domain: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    """미확인 목록 (심각도순, 페이징)."""
    conn, _ = project.load(settings, project_id)
    return unknowns.list_(conn, status=status, severity=severity, domain=domain, offset=offset, limit=limit)


@mcp.tool()
@enveloped("findings.evidence_get")
def oss_evidence_get(project_id: str, evidence_id: str) -> dict:
    """근거 상세 (제공자·연산·위치·한계)."""
    conn, _ = project.load(settings, project_id)
    return evidence.get(conn, evidence_id)
