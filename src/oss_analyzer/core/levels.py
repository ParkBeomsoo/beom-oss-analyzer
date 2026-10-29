"""검증 등급(L0~L3)과 언어별 능력표.

L0  구조 지도에만 존재하거나, 단일 위치를 읽고 내린 결론
L1  LSP 참조·호출 계층으로 관계가 확인된 결론
L2  데이터 흐름 엔진(Joern)으로 확인된 결론
L3  실행(커버리지)으로 실제 실행이 확인된 결론
"""
from __future__ import annotations

L0, L1, L2, L3 = 0, 1, 2, 3
LEVEL_NAMES = {L0: "L0", L1: "L1", L2: "L2", L3: "L3"}

# 근거 제공자 → 그 근거가 뒷받침할 수 있는 최고 등급
PROVIDER_LEVEL = {
    "agent-read": L0,
    "cbm": L0,
    "astgrep": L0,
    "convention": L0,  # 프레임워크 규약 (Next.js 파일 기반 라우팅 등)
    "resolver": L0,    # 동적 값 정적 해석
    "lsp": L1,
    "joern": L2,
    "coverage": L3,
}

# multilspy 가 지원하는 언어 중 우리가 허용한 것 (PHP 기본 서버 Intelephense 는 비공개 상용이라 제외)
LSP_LANGUAGES = {
    "python", "rust", "java", "typescript", "javascript", "go",
    "csharp", "kotlin", "dart", "ruby", "cpp", "c",
}

# Joern 프론트엔드가 있는 언어 (2단계에서 사용)
DATAFLOW_LANGUAGES = {
    "c", "cpp", "java", "javascript", "typescript", "python", "go",
    "kotlin", "php", "swift", "ruby", "csharp", "rust",
}
EXPERIMENTAL_DATAFLOW = {"rust"}


def parse_level(value: int | str) -> int:
    if isinstance(value, int):
        level = value
    else:
        level = int(str(value).upper().lstrip("L"))
    if level not in LEVEL_NAMES:
        raise ValueError(f"등급은 L0~L3 이어야 함: {value}")
    return level


def ceiling_for(language: str | None, engines: dict[str, bool]) -> int:
    """설치된 엔진 기준으로 해당 언어가 도달할 수 있는 최고 등급."""
    if not language:
        return L0
    if engines.get("coverage", False):
        return L3
    if engines.get("joern", False) and language in DATAFLOW_LANGUAGES:
        return L2
    if engines.get(f"lsp:{language}", False):
        return L1
    return L0
