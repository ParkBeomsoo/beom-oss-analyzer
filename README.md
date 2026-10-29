# oss-analyzer

오픈소스(또는 소스가 있는 저장소)를 **단위별로 빠짐없이 전수 분석**하는 MCP 툴킷.
REA(배포물 리버스 엔지니어링 툴킷)의 근거·미확인 설계를 소스 분석에 적용했다.

- 모든 결론에 **근거(파일:줄, 고정 커밋, 스니펫 해시)** — 기록할 때 실제 코드와 대조해 검증
- 확정 못 한 것은 **미확인 목록**으로 — 해결하려면 요구 등급 이상의 근거가 필요
- **숨은 빈틈 0** — 기준 목록은 git. 모든 파일 분류 + 모든 심볼 검토 + 엔진이 못 본 곳은 자동 미확인
- 검증 등급 **L0**(구조 지도·직접 읽기) / **L1**(LSP) / **L2**(데이터 흐름, Joern) / L3(실행 검증, 예정)
- **실제 URL 목록**: 동적으로 조립되는 경로까지 정적으로 펼침 (헬퍼 함수 호출부의 경로 표, 설정 기본값, 마운트·클래스 접두)

## 구성

```
Claude Code ──MCP──▶ oss-analyzer (Python, Apache-2.0)
서브에이전트 ──CLI──┘  ├ 원장 SQLite  ~/.oss-analyzer/projects/<id>/ledger.db (병렬 프로세스 안전)
                      ├ 규칙팩       src/oss_analyzer/rulepacks/*.yml (진입점)
                      └ 외부 엔진 (서브프로세스, 필요할 때만 기동)
                         git · codebase-memory-mcp(구조 지도) · ast-grep(진입점)
                         multilspy→언어 서버(LSP) · Joern(데이터 흐름)
```

| 기능 | 구현 |
|---|---|
| 파일 전수 분류 | git 기준, 셔뱅·dotfile·비밀 파일 규칙, 미분류는 완료 게이트에서 실패 |
| 심볼 전수 배정 | 코드·테스트 심볼 + MyBatis SQL 구문(XML 은 태그 대신 구문 단위) |
| 진입점 | Axum·Spring·Express·FastAPI/Flask·Gin·clap 규칙팩, Next.js 파일 기반 라우팅(그룹·병렬·가로채기·비활성 폴더) |
| 경로 해석 | `format!`/템플릿/연결, 지역·저장소 바인딩, 매개변수·반복 변수 → 호출부 인자 표, 설정 기본값 가정 표시 |
| 호출 관계 | 구조 지도 간선(L0, 약한 간선 숨김) + LSP 호출 계층(L1) + Java 매퍼 ↔ MyBatis 구문 연결 |
| 값 흐름 | Joern reachableByFlows (L2) — Java 는 delombok 을 타입 정보에만 써서 원본 줄 번호 유지 |
| 비밀 보호 | 비밀 파일 미열람·구조 지도 제외, 코드 출력 시 자격증명 값 가림(YAML 키 경로 인식) |

## 설치

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/oss-analyzer doctor
```

외부 엔진은 `doctor` 의 `install_hints` 를 따른다.
- codebase-memory-mcp: [릴리스](https://github.com/DeusData/codebase-memory-mcp/releases) zip 을 `checksums.txt` 로 검증 후 `~/.oss-analyzer/bin/`
- ast-grep: `npm i -g @ast-grep/cli`
- Rust: `rustup component add rust-analyzer` / Java: JDK 17+ / Python: 자동
- TS·JS: Node.js + `npm i -g typescript-language-server` + **툴킷 전용 TypeScript 5** `npm install --prefix ~/.oss-analyzer/node typescript@5`
  (TypeScript 7 은 tsserver.js 가 없어 호환 안 됨. 분석 대상에 npm install 은 하지 않는다)
- Joern: [릴리스](https://github.com/joernio/joern/releases) `joern-cli-<os>.zip`(sha512 검증) → `~/.oss-analyzer/engines/joern-cli`, JDK 21+

Claude Code 등록:

```bash
claude mcp add -s user oss-analyzer -- <설치 경로>/.venv/Scripts/oss-analyzer-mcp.exe
```

스킬: `skills/oss-analyze/` 를 `~/.claude/skills/oss-analyze/` 로 복사 (CLI 사용법은 `CLI.md`).

## 사용

Claude Code 에서 "이 저장소 전수 분석해줘" → `oss-analyze` 스킬이 절차를 따른다.
큰 저장소는 서브에이전트 여러 개가 각자 agent id 로 `next` 를 호출해 병렬 분석한다 (임대로 충돌 방지).

```bash
oss-analyzer open <경로|URL> [--ref <커밋>]
oss-analyzer inventory <project_id>
oss-analyzer features <project_id>          # 실제 URL 카탈로그
oss-analyzer dataflow <project_id> --build-only   # L2 용 CPG 미리 생성 (처음 한 번 오래 걸림)
oss-analyzer coverage <project_id>
oss-analyzer export <project_id>
```

## 실측 — Meilisearch v1.56.0 (2026-10-29)

[Meilisearch](https://github.com/meilisearch/meilisearch) (Rust, actix-web, 파일 1,750개, Rust 721개)로 검증했다.

| 항목 | 결과 |
|---|---|
| 인벤토리 | 약 1분 20초 — 파일 1,750개 전부 분류(미분류 0), 심볼 9,644개 → 단위 404개 |
| 진입점 | 선언형 라우트 표 매크로 26개(`sub()` 중첩·`macro_rules!` 반복 포함) 해석 → HTTP 엔드포인트 143개 |
| 라우트 정확도 | 공식 릴리스의 `meilisearch-openapi.json` 과 대조 **재현율 100%, 정밀도 100% (143/143)** (`eval/routes_vs_openapi.py`) |
| 단위 분석 | 핵심 8개 단위(인증·API 키·루트 라우트·검색·작업·인덱스 스왑)를 서브에이전트 2개로 분석 |
| 사실 평가 | 코드에서 확인한 사실 7개 모두 근거와 함께 결론에 포함 (`eval/meilisearch.yml`, 7/7) |
| 독립 검토 | 중요 결론 41개 전수 검토 — 확인 35, 반려 6 (85%). 반려 4건은 같은 분기 요약 오류의 반복, 주석 과장 1, 집계 수치 1 → `SKILL.md` 규칙에 반영 |

재현:
```bash
git clone --depth 1 --branch v1.56.0 https://github.com/meilisearch/meilisearch.git
oss-analyzer open ./meilisearch && oss-analyzer inventory meilisearch-47567abc
curl -LO https://github.com/meilisearch/meilisearch/releases/download/v1.56.0/meilisearch-openapi.json
python eval/routes_vs_openapi.py meilisearch-47567abc meilisearch-openapi.json
```

검증하면서 드러나 고친 것: Windows 긴 경로 clone 실패, 대상 저장소의 `rust-toolchain.toml` 때문에 rust-analyzer 가
즉시 죽고 클라이언트가 무한 대기하던 문제(툴체인 고정 + 기동 감시), 죽은 프로세스의 잠금 회수(PID),
dev-dependency 로 인한 프레임워크 오탐, 테스트 스냅샷 860개 미분류, 코드 식별자(`token: &str`)까지 가리던 비밀값 마스킹.

## 안전 원칙

- **분석 대상 코드를 실행하지 않는다.** rust-analyzer 의 빌드 스크립트·proc-macro·cargo check 를 꺼서 띄운다
  (multilspy 기본값은 켜져 있어 남의 코드가 컴파일·실행됨). 그래서 proc-macro 생성 항목은 미확인으로 남긴다.
- **비밀 파일**은 `secret` 범주 — 읽지 않고, 근거로 쓸 수 없고, 구조 지도 엔진에도 넘기지 않는다(`.cbmignore`).
  일반 파일 속 비밀값(설정의 password·key·token, URL 자격증명, 토큰 패턴)은 코드 출력 시 `****` 로 가린다.
- 분석은 **전용 clone** 에서 고정 커밋으로만 한다 — 원본 작업 트리(커밋 안 된 변경 포함)는 반영되지 않는다.

## 알려진 한계

| 한계 | 처리 |
|---|---|
| CLI 는 호출마다 언어 서버를 새로 띄움 (jdtls 1~3분) | 같은 프로젝트·언어는 프로세스 간 줄 세움. MCP 모드는 서버 재사용 |
| Joern CPG 첫 생성이 느림 (1만 줄 Java 약 15분) | 캐시, `--build-only` 로 미리 |
| 실행 검증(L3) 없음 | 3단계 격리 실행 + 커버리지 |
| Java LSP(jdtls)는 Maven/Gradle 을 가져오며 빌드 설정을 해석함 | 신뢰할 수 없는 저장소는 격리 환경에서 (3단계) |
| PHP LSP | multilspy 기본(Intelephense)이 비공개 상용이라 제외 |
| 설정값에 따라 바뀌는 경로 | 기본값으로 펼치고 `config` 미확인으로 표시 |
| 규칙팩 없는 프레임워크 | 감지 시 자동 미확인 (`missing_rulepacks`) |

## 라이선스

Apache-2.0. 의존성·외부 엔진은 [THIRD_PARTY.md](THIRD_PARTY.md).
