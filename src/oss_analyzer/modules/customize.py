"""커스텀 도구 — 영향도 · 확장 포인트 · 커스텀 원장 · 업스트림 버전 비교."""
from __future__ import annotations

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import customize, extpoints, project
from oss_analyzer.server import lsp_pool, mcp, settings


@mcp.tool()
@enveloped("customize.impact")
def oss_impact_analyze(project_id: str, targets: list[str], depth: int = 4, verify: bool = False) -> dict:
    """대상을 바꾸면 어디가 영향을 받는지: 호출자(전이) · 인터페이스 구현 · 이름 일치 후보 · 진입점 · 테스트 · 단위 · 다시 볼 결론.

    Args:
        targets: 심볼 id(s_…) / "경로:줄" / 파일 경로 / 이름 또는 정규 이름 꼬리("pricing.calc_total")
        verify: True 면 대상마다 LSP incoming 을 돌려 L1 근거를 남기고 구조 지도가 놓친 호출자를 보탬
    """
    conn, proj = project.load(settings, project_id)
    return customize.impact(conn, proj, targets, depth, verify, lsp_pool if verify else None)


@mcp.tool()
@enveloped("customize.extension_points")
def oss_extension_points(project_id: str, kind: str | None = None, path: str | None = None,
                         offset: int = 0, limit: int = 200) -> dict:
    """확장 포인트 목록 (interface / registry / middleware / config-key / overridable-bean / spi). 코어 수정보다 먼저 검토."""
    conn, _ = project.load(settings, project_id)
    return extpoints.list_(conn, kind, path, offset, limit)


@mcp.tool()
@enveloped("customize.record")
def oss_customization_record(project_id: str, description: str, targets: list[str], kind: str = "modify",
                             extension_point_id: str | None = None, author: str = "agent") -> dict:
    """우리가 할(한) 변경을 원장에 기록. 대상 심볼의 시그니처·본문 해시를 앵커로 저장 → 버전 비교 때 충돌 계산.

    Args:
        kind: extend(확장 포인트로 추가) / modify / replace / config / remove
    결과에 영향도 요약과, 수정 대신 쓸 수 있는 근처 확장 포인트(consider_instead)가 붙는다.
    """
    conn, proj = project.load(settings, project_id)
    return customize.record(conn, proj, description=description, targets=targets, kind=kind,
                            extension_point_id=extension_point_id, author=author)


@mcp.tool()
@enveloped("customize.list")
def oss_customization_list(project_id: str, status: str | None = None) -> dict:
    """커스텀 원장 (planned / applied / dropped)."""
    conn, _ = project.load(settings, project_id)
    return customize.list_customizations(conn, status)


@mcp.tool()
@enveloped("customize.update")
def oss_customization_update(project_id: str, customization_id: str, status: str, note: str, actor: str = "agent") -> dict:
    """커스텀 상태 변경 (planned → applied / dropped). note 필수."""
    conn, _ = project.load(settings, project_id)
    return customize.update_customization(conn, customization_id, status=status, note=note, actor=actor)


@mcp.tool()
@enveloped("customize.compare_versions")
def oss_compare_versions(base_project_id: str, head_project_id: str, limit: int = 300) -> dict:
    """두 버전(각각 oss_repo_open --ref 로 연 프로젝트) 비교: 파일·심볼(추가/제거/이름 변경/시그니처/본문)·라우트·확장 포인트,
    base 커스텀의 충돌 지점, 다시 검증할 결론, 바뀐 계약의 호출자, 그대로 재사용 가능한 완료 단위."""
    return customize.compare_versions(settings, base_project_id, head_project_id, limit)
