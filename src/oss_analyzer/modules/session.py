"""세션·환경 도구."""
from __future__ import annotations

from pathlib import Path

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import doctor, project
from oss_analyzer.server import lsp_pool, mcp, settings


@mcp.tool()
@enveloped("session.doctor")
def oss_doctor(languages: list[str] | None = None) -> dict:
    """엔진 설치 여부·버전과 설치 안내. languages 를 주면 그 언어의 LSP 만 점검.

    Args:
        languages: 점검할 언어 목록 (예: ["rust", "python"]). 생략 시 지원 언어 전체.
    """
    return doctor.run(settings, languages)


@mcp.tool()
@enveloped("session.repo_open")
def oss_repo_open(source: str, ref: str | None = None, name: str | None = None) -> dict:
    """분석할 저장소를 열고 커밋을 고정한다. 분석 전용 clone 을 만들어 원본 작업 트리와 분리.

    Args:
        source: 로컬 경로 또는 git URL
        ref: 고정할 브랜치·태그·커밋 (생략 시 HEAD)
        name: 프로젝트 이름 (생략 시 저장소 이름). 실제 id 는 <name>-<커밋 앞 8자리>
    """
    return project.open_repo(settings, source, ref, name)


@mcp.tool()
@enveloped("session.repo_list")
def oss_repo_list() -> dict:
    """열어 둔 프로젝트 목록."""
    return {"projects": project.list_projects(settings)}


@mcp.tool()
@enveloped("session.repo_status")
def oss_repo_status(project_id: str) -> dict:
    """프로젝트 메타데이터(원본·커밋·인벤토리 시각·엔진 능력)."""
    _, proj = project.load(settings, project_id)
    return proj


@mcp.tool()
@enveloped("session.repo_close")
def oss_repo_close(project_id: str) -> dict:
    """언어 서버와 DB 연결을 닫는다. 원장·clone 은 지우지 않음 (다음에 이어서 분석 가능)."""
    _, proj = project.load(settings, project_id)
    lsp_pool.close_repo(Path(proj["repo_dir"]))
    project.close(project_id)
    return {"closed": project_id}


@mcp.tool()
@enveloped("session.setup_plan")
def oss_setup_plan(languages: list[str] | None = None) -> dict:
    """엔진 설치 계획 — 구성 요소별 상태·라이선스·출처·설치 명령. 아무것도 설치하지 않는다.
    설치는 사용자가 터미널에서 `oss-analyzer setup --apply <id...>` 로 직접 승인해 실행한다 (체크섬 검증)."""
    from oss_analyzer.core import setup

    return setup.plan(settings, languages)
