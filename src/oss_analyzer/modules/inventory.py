"""인벤토리 도구."""
from __future__ import annotations

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import inventory, loop, project
from oss_analyzer.server import mcp, settings


@mcp.tool()
@enveloped("inventory.build")
def oss_inventory_build(project_id: str, rebuild: bool = False) -> dict:
    """고정 커밋의 전 파일 분류 + 구조 지도 인덱싱 + 심볼 전수 배정 + 단위 분할 + 진입점 수집.
    엔진이 못 본 곳(파싱 실패·규칙팩 없는 프레임워크·동적 경로·바이너리)은 미확인으로 자동 등록.

    Args:
        project_id: oss_repo_open 이 돌려준 id
        rebuild: 다시 만들기 (진행 중·완료 단위가 있으면 거부)
    """
    conn, proj = project.load(settings, project_id)
    return inventory.build(conn, settings, proj, rebuild)


@mcp.tool()
@enveloped("inventory.capabilities")
def oss_capabilities(project_id: str) -> dict:
    """언어별로 쓸 수 있는 엔진과 단위 상한 등급."""
    _, proj = project.load(settings, project_id)
    return proj.get("capabilities") or {}


@mcp.tool()
@enveloped("inventory.units_list")
def oss_units_list(project_id: str, status: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    """단위 목록 (status: pending / in_progress / done)."""
    conn, _ = project.load(settings, project_id)
    return loop.list_units(conn, status, offset, limit)


@mcp.tool()
@enveloped("inventory.file_classify")
def oss_file_classify(project_id: str, paths: list[str], category: str, reason: str) -> dict:
    """규칙으로 분류되지 않은(unclassified) 파일 등을 직접 분류. 사유 필수.

    Args:
        category: doc / generated / vendored / secret / binary / asset / code / test
        reason: 이렇게 분류한 이유
    """
    conn, _ = project.load(settings, project_id)
    return loop.classify_files(conn, paths=paths, category=category, reason=reason)
