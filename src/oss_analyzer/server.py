"""MCP 서버 인스턴스와 공용 상태(설정·LSP 풀). modules/ 의 도구들이 이 인스턴스에 등록된다."""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from oss_analyzer import __version__
from oss_analyzer.adapters.lsp import LspPool
from oss_analyzer.config import load_settings

settings = load_settings()
lsp_pool = LspPool(settings.lsp_idle_seconds, settings.lsp_timeout_seconds)

mcp = MCPServer(
    "oss-analyzer",
    version=__version__,
    instructions=(
        "오픈소스 저장소를 단위별로 전수 분석하는 툴킷입니다. 도구 prefix: oss_*. "
        "흐름: oss_doctor → oss_repo_open → oss_inventory_build → (oss_unit_next → oss_unit_context → "
        "oss_finding_record / oss_symbols_mark / oss_unknown_record → oss_unit_complete) 반복 → oss_report_coverage → oss_report_export. "
        "모든 결론은 근거(파일:줄)가 있어야 하고, 확정 못 한 것은 oss_unknown_record 로 남깁니다. "
        "호출 관계를 결론에 쓰려면 oss_lsp_references / oss_lsp_call_hierarchy 가 돌려준 evidence_id(L1)를 인용하세요. "
        "비밀 파일(secret 범주)은 읽거나 인용하지 않습니다. 자세한 절차는 oss-analyze 스킬을 따르세요."
    ),
)
