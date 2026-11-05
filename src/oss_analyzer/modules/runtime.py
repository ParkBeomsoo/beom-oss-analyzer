"""실행 검증(L3)·라이선스 도구."""
from __future__ import annotations

from pathlib import Path

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import license as license_mod
from oss_analyzer.core import project, runtime
from oss_analyzer.server import mcp, settings


@mcp.tool()
@enveloped("runtime.ingest")
def oss_runtime_ingest(project_id: str, report_path: str, roots: list[str] | None = None, tool: str | None = None) -> dict:
    """커버리지 리포트(coverage.py JSON / LCOV / Istanbul JSON / Cobertura·JaCoCo XML)를 심볼 단위 L3 근거로 적재.

    Args:
        roots: 리포트의 절대 경로를 저장소 상대 경로로 바꿀 때 잘라낼 접두 (테스트를 돌린 디렉터리)
    리포트는 반드시 프로젝트 고정 커밋과 같은 코드에서 만든 것이어야 한다.
    """
    conn, proj = project.load(settings, project_id)
    return runtime.ingest(conn, proj, Path(report_path), tool=tool, roots=roots)


@mcp.tool()
@enveloped("runtime.run")
def oss_runtime_run(project_id: str, command: str, report: str, trusted: bool = False, image: str | None = None,
                    network: bool = False, timeout: int = 1800) -> dict:
    """저장소 사본에서 테스트·실행 명령을 돌리고 커버리지를 적재 (L3).

    Docker 데몬이 있으면 컨테이너(image 필수, 기본 네트워크 차단)에서, 없으면 trusted=True 일 때만 로컬 임시 사본에서 실행.
    신뢰할 수 없는 저장소를 trusted=True 로 돌리지 말 것 (내 PC 에서 그 코드가 그대로 실행됨).
    Args:
        report: 명령이 만드는 리포트 경로 (사본 루트 기준, 예: coverage.json)
    """
    conn, proj = project.load(settings, project_id)
    return runtime.run(conn, proj, settings, command, report=report, trusted=trusted, image=image,
                       timeout=timeout, network=network)


@mcp.tool()
@enveloped("license.scan")
def oss_license_scan(project_id: str) -> dict:
    """라이선스 스캔(SPDX 헤더·LICENSE 문구·매니페스트). 저장소 라이선스와 다른 파일·디렉터리는 미확인으로 등록."""
    conn, proj = project.load(settings, project_id)
    return license_mod.scan(conn, proj)
