"""리포트 도구."""
from __future__ import annotations

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import project, report
from oss_analyzer.server import mcp, settings


@mcp.tool()
@enveloped("report.coverage")
def oss_report_coverage(project_id: str) -> dict:
    """2축(파일·심볼) 커버리지와 완료 조건(gates). complete=true 여야 '전수 분석 완료'."""
    conn, _ = project.load(settings, project_id)
    return report.coverage(conn)


@mcp.tool()
@enveloped("report.features")
def oss_report_features(project_id: str, offset: int = 0, limit: int = 200) -> dict:
    """기능 카탈로그 (진입점: http / http-mount / cli / main / scheduler / listener / background-task)."""
    conn, _ = project.load(settings, project_id)
    return report.features(conn, offset, limit)


@mcp.tool()
@enveloped("report.export")
def oss_report_export(project_id: str) -> dict:
    """리포트를 Markdown + JSON 으로 내보낸다. 경로를 돌려줌."""
    conn, proj = project.load(settings, project_id)
    return report.export(conn, settings, proj)
