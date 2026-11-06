# Third-party components

## pip 의존성 (함께 설치됨)

| 패키지 | 라이선스 | 용도 |
|---|---|---|
| mcp (Model Context Protocol Python SDK) | MIT | MCP 서버 |
| multilspy (Microsoft) | MIT | 언어 서버 제어 |
| PyYAML | MIT | 규칙팩 읽기 |

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
| Joern | Apache-2.0 | 데이터 흐름 |
| coverage.py | Apache-2.0 | Python 실행 검증(L3), `setup` 으로 설치 |
| ScanCode (선택) | Apache-2.0, 데이터 CC-BY-4.0 | 정밀 라이선스 스캔 — 내장 스캐너로 부족할 때만 별도 설치 |
| Docker Engine (Moby, Debian `docker.io`) | Apache-2.0 | 남의 코드 격리 실행 — Windows 는 WSL 배포판 `oss-sandbox` 안에서 (`setup --apply sandbox`). Docker Desktop 은 쓰지 않아도 됨 (일정 규모 이상 회사의 업무 사용은 유료 구독 조건) |
| WSL Debian 배포판 | 구성 패키지별 자유 소프트웨어 라이선스 | 위 Docker Engine 의 실행 환경 (외부, 사용자 PC 에 설치) |

multilspy 의 PHP 기본 서버(Intelephense)는 비공개 상용이라 사용하지 않는다.
