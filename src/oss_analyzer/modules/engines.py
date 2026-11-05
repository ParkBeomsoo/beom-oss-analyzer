"""엔진 질의 도구 — LSP 결과는 서버가 근거(L1)로 기록한 뒤 evidence_id 를 돌려준다."""
from __future__ import annotations

from oss_analyzer.common.response import enveloped
from oss_analyzer.core import engines, project
from oss_analyzer.server import lsp_pool, mcp, settings


@mcp.tool()
@enveloped("engines.lsp_references")
def oss_lsp_references(project_id: str, path: str, line: int, column: int) -> dict:
    """심볼 참조 위치 전체 (LSP). 줄·열은 1부터. 결과는 L1 근거로 기록됨."""
    conn, proj = project.load(settings, project_id)
    return engines.lsp_references(conn, lsp_pool, proj, path, line, column)


@mcp.tool()
@enveloped("engines.lsp_definition")
def oss_lsp_definition(project_id: str, path: str, line: int, column: int) -> dict:
    """심볼 정의 위치 (LSP). 결과는 L1 근거로 기록됨."""
    conn, proj = project.load(settings, project_id)
    return engines.lsp_definition(conn, lsp_pool, proj, path, line, column)


@mcp.tool()
@enveloped("engines.lsp_call_hierarchy")
def oss_lsp_call_hierarchy(project_id: str, path: str, line: int, column: int, direction: str = "incoming") -> dict:
    """호출 계층 (LSP callHierarchy). direction: incoming(누가 부르나) / outgoing(무엇을 부르나). L1 근거로 기록됨.

    Args:
        line, column: 함수 이름 위치 (1부터)
    """
    conn, proj = project.load(settings, project_id)
    return engines.lsp_call_hierarchy(conn, lsp_pool, proj, path, line, column, direction)


@mcp.tool()
@enveloped("engines.dataflow_build")
def oss_dataflow_build(project_id: str, language: str | None = None, rebuild: bool = False,
                       scope: list[str] | None = None) -> dict:
    """데이터 흐름용 CPG 를 만든다 (Joern). 처음 한 번 오래 걸리고 이후엔 캐시. language 생략 시 주 언어.
    테스트·테스트 데이터·외부 코드는 항상 제외. scope(경로 접두 목록)를 주면 그 아래만 — 큰 저장소에서 시간 단축."""
    from oss_analyzer.core import dataflow

    conn, proj = project.load(settings, project_id)
    return dataflow.build(conn, settings, proj, language, rebuild, scope)


@mcp.tool()
@enveloped("engines.dataflow_query")
def oss_dataflow_query(project_id: str, queries: list[dict], language: str | None = None,
                       scope: list[str] | None = None) -> dict:
    """값 흐름(L2): 소스(함수 정의 줄 → 매개변수, 또는 호출 줄 → 인자)에서 sink(호출 이름 정규식)까지 흐르는지.

    Args:
        queries: [{"path": "...", "line": 91, "sink": "select.*|insert.*", "max_flows": 20}]
    결과의 evidence_id 는 L2 근거. 흐름 0건은 '흐름 없음'의 증명이 아님 (limitations 참고).
    """
    from oss_analyzer.core import dataflow

    conn, proj = project.load(settings, project_id)
    return dataflow.query(conn, settings, proj, queries, language, scope)


@mcp.tool()
@enveloped("engines.graph_query")
def oss_graph_query(project_id: str, cypher: str, max_rows: int = 200) -> dict:
    """구조 지도 읽기 전용 Cypher 질의 (codebase-memory-mcp). 이름 기반 해석이라 L0."""
    _, proj = project.load(settings, project_id)
    return engines.graph_query(settings, proj, cypher, max_rows)
