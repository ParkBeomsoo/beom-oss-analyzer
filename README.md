# oss-analyzer

오픈소스(또는 소스가 있는 저장소)를 **단위별로 빠짐없이 전수 분석**하는 MCP 툴킷.
REA(배포물 리버스 엔지니어링 툴킷)의 근거·미확인 설계를 소스 분석에 적용했다.

- 모든 결론에 **근거(파일:줄, 고정 커밋, 스니펫 해시)** — 기록할 때 실제 코드와 대조해 검증
- 확정 못 한 것은 **미확인 목록**으로 — 해결하려면 요구 등급 이상의 근거가 필요
- **숨은 빈틈 0** — 기준 목록은 git. 모든 파일 분류 + 모든 심볼 검토 + 엔진이 못 본 곳은 자동 미확인
- 검증 등급 **L0**(구조 지도·직접 읽기) / **L1**(LSP) / **L2**(데이터 흐름, Joern) / **L3**(실행 커버리지)
- **커스텀 작업 흐름**: 영향도 분석 · 확장 포인트 · 커스텀 원장 · 업스트림 버전 비교(충돌 지점 자동 산출)
- **포팅**: 기능 하나를 내 저장소로 옮기거나 다른 언어로 다시 만들 때 — 옮길 범위(슬라이스) · 항목별 매핑 원장 · 원본 대비 차등 검증(HTTP·CLI·함수 단위, 언어 무관) · 원본 커버리지로 케이스 충분성 확인 · 입력 자동 변형 · 동시 요청 검사 · 원본은 Docker 격리 실행
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
| 값 흐름 | Joern reachableByFlows (L2) — 테스트·외부 코드를 뺀 사본 트리로 CPG, `--scope` 로 범위 한정. Java 는 delombok 을 타입 정보에만 |
| 라우터 마운트 | FastAPI include_router·APIRouter prefix, Express app.use+require, Gin Group, Axum nest, Spring 클래스 접두 |
| 실행 검증(L3) | coverage.py JSON·LCOV·Istanbul·Cobertura·JaCoCo → 심볼 단위 실행 근거. 실행은 Docker(네트워크 차단) 또는 `--trusted` 로컬 사본. Windows 는 WSL 배포판 `oss-sandbox` 의 Docker Engine 을 자동 사용 |
| 영향도 | 호출 간선(전이) + 인터페이스↔구현 + 이름 일치 후보(동적 디스패치) → 영향받는 진입점·테스트·단위·결론. `--verify` 면 LSP 로 대조(L1) |
| 확장 포인트 | 추상 타입+구현체, 등록 함수+등록 위치, 미들웨어, 설정 키, Spring 교체 가능 빈, SPI. 동적 로딩은 미확인 |
| 버전 비교 | 심볼 추가/제거/이름 변경/시그니처/본문, 모듈 최상위(상수) 변경, 라우트·확장 포인트 증감, 커스텀 앵커 충돌, 다시 볼 결론, 재사용 가능 단위 |
| 라이선스 | SPDX 헤더·LICENSE 문구·매니페스트, 하위 디렉터리 범위, 저장소와 다른 계열은 미확인 |
| 포팅 슬라이스 | 진입점→호출·인터페이스 디스패치·등록(+import 배선)·모듈 상태·설정 키·외부 import·명세 테스트·라이선스 |
| 포팅 검증 | 원본 응답을 골든으로 저장 → 대상(어떤 언어든)과 비교. id 같은 값은 정규식, 무시 경로, 상태만 비교 지원. 통과 시 L3 근거 |
| 포팅 검증 범위 | 원본 커버리지로 슬라이스 함수가 모두 실행됐는지(게이트)·안 거친 줄 / 입력 변형(경계값·타입·삭제·빈 값) / 함수 직접 호출(.py·.js 내장 하네스, 그 밖은 stdin·stdout JSON 명령) / 동시 요청 / 대상 서버가 죽으면 그 케이스로 기록 후 재기동 |
| 포팅 매핑 추적 | ported 대상의 줄 내용을 기록 → 줄이 밀리거나 바뀌면 게이트 실패, `port-map --follow` 로 이동 위치 갱신. 의도적 차이는 `port-accept`(사유 필수)로 따로 집계 |
| 포팅 지킴이 | AI 가 혼자 정하면 안 되는 것(차이 허용·생략·케이스 삭제/변경)은 **제안**으로만 기록 → 사용자가 MCP 확인 창 또는 터미널 직접 입력(`port-approve`, 파이프 실행 거부)으로 승인해야 통과, 승인은 본 내용의 지문에 묶임. 검증 때 대상 코드 지문을 남겨 **검증 뒤 코드가 바뀌면 검증·완료 무효**. 원장 쓰기는 해시 사슬로 기록 → 원장·골든을 툴킷 밖에서 고치면 게이트 실패. 매 응답에 `next_step`, 완료는 `port-finish` 판정으로만 |
| 비밀 보호 | 비밀 파일 미열람·구조 지도 제외, 코드 출력 시 자격증명 값 가림(YAML 키 경로 인식, 키 이름 없는 고엔트로피 문자열) |

## 설치

툴킷을 PATH 에 설치해서 어느 폴더·세션에서나 `oss-analyzer` 로 부른다 (스킬·문서는 설치 경로를 적지 않는다):

```bash
pipx install <이 저장소 경로 또는 git URL>      # 또는: uv tool install <같은 값>
oss-analyzer doctor                              # 엔진·CLI·스킬·MCP 등록 상태
```

툴킷을 고치며 쓸 때는 저장소에서 `py -3.11 -m venv .venv` → `pip install -e ".[dev]"` 후 venv 를 활성화하거나
`pipx install --editable .` 로 설치한다.

`setup` 이 계획을 보여 주고, 고른 것만 설치한다 (REA 의 setup 처럼 계획 → 승인 → 실행. 내려받는 파일은 배포처 체크섬으로 검증):

```bash
oss-analyzer setup --languages python typescript   # 계획만 (아무것도 바꾸지 않음)
oss-analyzer setup --apply codebase-memory-mcp joern lsp:python coverage
oss-analyzer setup --apply skill mcp-register      # Claude Code 연동 (아래)
oss-analyzer setup --remove skill mcp-register     # 되돌리기 — 이 툴킷이 설치·등록한 것만
oss-analyzer setup --update                        # 툴킷 업그레이드 뒤 — 이미 설치한 스킬만 새 버전으로 (새로 설치하지 않음)
oss-analyzer setup --apply skill mcp-register --scope project --project-dir <프로젝트>   # 한 프로젝트에서만
```

언어 런타임(JDK·Node.js·Go·Rust)은 직접 설치한다. 격리 실행용 Docker 는 Windows 에서 `oss-analyzer setup --apply sandbox` 가 WSL 배포판(`oss-sandbox`, Debian)에 Docker Engine(Apache-2.0)을 설치한다 — Docker Desktop(일정 규모 이상 회사의 업무 사용은 유료 구독)이 필요 없다. 네이티브 `docker` 데몬이 있으면 그것을 먼저 쓴다(`OSS_ANALYZER_CONTAINER=auto|docker|wsl|none`). 수동 설치 경로:
- codebase-memory-mcp: [릴리스](https://github.com/DeusData/codebase-memory-mcp/releases) zip 을 `checksums.txt` 로 검증 후 `~/.oss-analyzer/bin/`
- ast-grep: `npm i -g @ast-grep/cli`
- Rust: `rustup component add rust-analyzer` / Java: JDK 17+ / Python: 자동
- TS·JS: Node.js + `npm i -g typescript-language-server` + **툴킷 전용 TypeScript 5** `npm install --prefix ~/.oss-analyzer/node typescript@5`
  (TypeScript 7 은 tsserver.js 가 없어 호환 안 됨. 분석 대상에 npm install 은 하지 않는다)
- Joern: [릴리스](https://github.com/joernio/joern/releases) `joern-cli-<os>.zip`(sha512 검증) → `~/.oss-analyzer/engines/joern-cli`, JDK 21+

Claude Code 연동 (`setup --apply skill mcp-register`):
- **스킬**: 패키지에 들어 있는 `oss_analyzer/skills/oss-analyze/`(SKILL.md·CLI.md)를 Claude Code 개인 스킬 폴더
  (`CLAUDE_CONFIG_DIR` 또는 `~/.claude` 아래 `skills/oss-analyze`)로 복사하고 버전·지문을 남긴다.
  툴킷을 업데이트하면 `doctor` 가 `outdated` 로 알려 주고, 다시 apply 하면 이전 사본을 `~/.oss-analyzer/backups` 에 두고 교체한다.
  같은 이름의 스킬을 직접 만들어 둔 경우(소유 표시 없음)는 덮어쓰지 않는다.
- **MCP**: `claude mcp add -s user oss-analyzer -- <이 설치의 python> -m oss_analyzer.main` — 경로는 setup 이 실행 중인 설치에서 구한다.
  다른 설치를 가리키는 등록이 이미 있으면 바꾸지 않고 알려 준다.
- **범위**: 기본 `--scope user`(이 사용자의 모든 프로젝트). `--scope project` 는 그 프로젝트의 `.claude/skills` 와
  `.mcp.json` 에만 넣는다 — 분석 도구를 특정 작업 폴더에서만 보이게 할 때. (도구가 안 보일 뿐, 같은 계정의 셸에서
  `oss-analyzer` 명령은 그대로 쓸 수 있다)
- MCP 없이 CLI 만 써도 된다 — 스킬이 CLI 절차(`CLI.md`)를 함께 안내하고, 포팅 지킴이(승인·완료 판정)는 CLI 에서도 똑같이 동작한다.

Claude Code 플러그인으로 받기 (스킬 + MCP 한 번에, 툴킷 자체는 위처럼 먼저 설치):

```bash
claude plugin marketplace add ParkBeomsoo/beom-oss-analyzer
claude plugin install beom-oss-analyzer@beom-oss-analyzer
```

플러그인의 MCP 는 `plugin/launch_mcp.py` 가 이미 설치된 툴킷을 찾아 띄운다 (아무것도 설치하지 않음 — 없으면 설치 안내를
남기고 종료, 버전이 플러그인과 다르면 경고). 플러그인과 `setup --apply skill mcp-register` 는 같은 것을 넣으므로 하나만 쓴다.

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

# 커스텀
oss-analyzer extpoints <project_id>                          # 코어 수정 전에 볼 확장 포인트
oss-analyzer impact <project_id> pricing.calc_total --verify # 영향받는 진입점·테스트 (LSP 대조)
oss-analyzer custom-add <project_id> --description "..." --kind modify --targets pricing.calc_total
oss-analyzer open <경로> --ref v2 && oss-analyzer inventory <새 id>
oss-analyzer compare <기준 id> <새 id> --save               # 업스트림 변경 + 커스텀 충돌

# 실행 검증(L3) · 라이선스
oss-analyzer runtime-ingest <project_id> coverage.json --root <테스트 돌린 경로>
oss-analyzer runtime-run <project_id> --image python:3.11-slim --report coverage.json --command "..."
oss-analyzer license <project_id>

# 포팅 (내 저장소로 옮기기 / 다른 언어로 다시 만들기)
oss-analyzer slice <project_id> "POST /api/v1/orders"         # 옮길 범위
oss-analyzer port-create <project_id> --name 주문 --targets "POST /api/v1/orders" --language javascript --target-root <내 저장소>
oss-analyzer port-capture <project_id> <port_id> --cases cases.json --source-url http://127.0.0.1:18001 \
    --source-image <원본 의존성 이미지> --source-cmd "python -m coverage run --data-file=.cov -m uvicorn app.main:app --host 0.0.0.0 --port 18001" \
    --coverage-cmd "python -m coverage json --data-file=.cov -o cov.json" --coverage-report cov.json \
    --explore 60                                                # 원본(Docker 격리) 응답 → 골든 + 케이스 충분성 + 입력 변형
oss-analyzer port-map <project_id> <port_id> --json map.json    # 항목별 ported(내 코드 경로:줄)/omitted/replaced
oss-analyzer port-verify <project_id> <port_id> --target-url http://127.0.0.1:18002 --target-cmd "node server.js" --concurrency 8
oss-analyzer port-map <project_id> <port_id> --follow           # 대상 코드 수정으로 줄이 밀린 매핑 갱신
oss-analyzer port-accept <project_id> <port_id> --case "*~json.items=null" --reason "원본 500 을 422 로 고침"   # 제안
oss-analyzer port-verify <project_id> <port_id> ... --target-coverage-cmd "<리포트 생성>" --target-coverage-report lcov.info  # 원본 계측 불가 시
oss-analyzer port-status <project_id> <port_id>                # 게이트 7개 + next_step
oss-analyzer port-approve <project_id> <port_id>               # 사용자가 터미널에서 직접 (차이 허용·생략·케이스 변경 승인)
oss-analyzer port-finish <project_id> <port_id>                # 게이트 전부 충족일 때만 완료 판정 발급
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
| L3 실행은 격리가 기본 | 컨테이너 엔진(네이티브 docker 또는 WSL `oss-sandbox`)이 없으면 `--trusted`(신뢰하는 저장소)만 로컬 임시 사본에서. 리포트 적재는 항상 가능 |
| 커버리지 = '실행됐다'만 증명 | 어떤 입력·분기인지는 별도 확인 (근거 limitations 에 기록) |
| 영향도의 이름 일치 갈래는 후보 | `possible=true` 로 구분, LSP·실행으로 확인해야 근거 |
| Python 데이터 흐름(Joern pysrc)은 동적 디스패치를 못 따라감 | 0건은 '흐름 없음'의 증명이 아님 — L3 로 보완 |
| jedi-language-server 는 callHierarchy 미지원 | 영향도 검증은 references 로 대체 (import·타입 표기도 섞임) |
| 포팅 동등성은 돌려 본 입력만 증명 | 입력 변형·원본 커버리지로 범위를 넓히고, 남은 범위(케이스 수·안 거친 줄·동시 실행 여부)를 근거 limitations 에 기록. 성능은 비교하지 않음(지연 시간은 참고값) |
| 커버리지는 정상 종료 때 기록됨 | 서버는 정상 종료 신호(Windows CTRL_BREAK, POSIX SIGINT, 컨테이너 docker stop) 후 강제 종료. 원본 서버 명령은 셸 연산자 없는 단일 명령 권장 |
| 포팅 검증 명령은 사용자가 준 것 | 원본은 Docker(네트워크 차단, 서버는 게시 포트만) 또는 `--trusted` 일 때 임시 사본. 대상(내 코드)은 로컬. 실행 환경 변수에서 토큰·키류 이름은 뺌 |
| 함수 단위 비교는 JSON 으로 오가는 값만 | 예외는 '던졌다'는 사실만 비교(이름·메시지는 언어마다 다름). 객체 인자가 필요하면 `{cmd}` 하네스 |
| 라이선스는 대표 문구 매칭 | 변형·이중 라이선스 문장, 의존성 라이선스는 ScanCode·패키지 도구로 (미확인으로 남김) |
| Java LSP(jdtls)는 Maven/Gradle 을 가져오며 빌드 설정을 해석함 | 신뢰할 수 없는 저장소는 격리 환경에서 |
| PHP LSP | multilspy 기본(Intelephense)이 비공개 상용이라 제외 |
| 설정값에 따라 바뀌는 경로 | 기본값으로 펼치고 `config` 미확인으로 표시 |
| 규칙팩 없는 프레임워크 | 감지 시 자동 미확인 (`missing_rulepacks`) |

## 라이선스

Apache-2.0. 의존성·외부 엔진은 [THIRD_PARTY.md](THIRD_PARTY.md).
