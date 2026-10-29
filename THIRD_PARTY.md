# Third-party components

## pip 의존성 (함께 설치됨)

| 패키지 | 라이선스 | 용도 |
|---|---|---|
| mcp (Model Context Protocol Python SDK) | MIT | MCP 서버 |
| multilspy (Microsoft) | MIT | 언어 서버 제어 |
| PyYAML | MIT | 규칙팩 읽기 |

## 설계 차용

| 출처 | 라이선스 | 범위 |
|---|---|---|
| REA (morluto/rea) | MIT | Evidence / ResidualUnknown 레코드 구조, 페이징 규약 (코드 복사 없이 구조만 재구현) |

## 외부 엔진 (사용자가 별도 설치, 서브프로세스로만 호출, 재배포하지 않음)

| 엔진 | 라이선스 | 용도 |
|---|---|---|
| git | GPL-2.0 | 파일 목록, 커밋 고정 |
| codebase-memory-mcp | MIT | 구조 지도 |
| ast-grep | MIT | 진입점 규칙팩 |
| rust-analyzer | MIT / Apache-2.0 | Rust LSP |
| jedi-language-server | MIT | Python LSP |
| typescript-language-server | MIT | TS/JS LSP |
| jdtls | EPL-2.0 | Java LSP (외부 프로세스로만) |
| Joern (2단계) | Apache-2.0 | 데이터 흐름 |
| ScanCode (3단계) | Apache-2.0, 데이터 CC-BY-4.0 | 라이선스 스캔 |

multilspy 의 PHP 기본 서버(Intelephense)는 비공개 상용이라 사용하지 않는다.
