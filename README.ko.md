# beom-oss-analyzer

[English](README.md) | **한국어** | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

**오픈소스를 빠짐없이 분석하고, 필요한 기능만 내 저장소로 옮겨 원본과 같은지 검증하는 툴킷**

![version](https://img.shields.io/badge/version-0.3.0-blue) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-use%20only%2C%20no%20modification-lightgrey)

남이 만든 기능을 "대충 읽고 따라 만들기" 대신, 코드 전체를 근거(파일:줄)와 함께 분석하고
옮긴 결과가 원본과 같은 동작인지 프로그램으로 확인합니다. Claude Code 같은 AI 에이전트가 절차를 따라 쓰도록
스킬·MCP 도구·CLI 를 함께 제공하고, AI 가 혼자 정하면 안 되는 일은 사람 승인 없이는 끝나지 않게 막습니다.

## 목차

- [무엇을 하나](#무엇을-하나)
- [핵심 개념](#핵심-개념)
- [요구 사항](#요구-사항)
- [설치](#설치)
- [데스크톱 앱과 자동 실행](#데스크톱-앱과-자동-실행)
- [빠른 시작 1: 저장소 전수 분석](#빠른-시작-1-저장소-전수-분석)
- [빠른 시작 2: 기능 옮기기와 검증](#빠른-시작-2-기능-옮기기와-검증)
- [빠른 시작 3: 요청사항으로 만들기](#빠른-시작-3-요청사항으로-만들기)
- [원본을 고쳐 쓰는 경우 (커스텀)](#원본을-고쳐-쓰는-경우-커스텀)
- [케이스 파일 형식](#케이스-파일-형식)
- [명령 요약](#명령-요약)
- [데이터와 저장 위치](#데이터와-저장-위치)
- [설정](#설정)
- [안전 원칙](#안전-원칙)
- [알려진 한계](#알려진-한계)
- [라이선스](#라이선스)

## 무엇을 하나

| 하고 싶은 일 | 툴킷이 하는 일 |
|---|---|
| **기능을 빠짐없이 파악** | 모든 파일을 분류하고 모든 함수를 검토 단위로 나눠, 결론마다 근거(파일:줄)를 붙입니다. 확정하지 못한 것은 "미확인 목록"으로 남깁니다. |
| **실제 API 목록** | 프레임워크별 규칙으로 HTTP·CLI 진입점을 찾고, 코드에서 조립되는 URL 까지 펼쳐 실제 경로 목록을 만듭니다. |
| **고치기 전 영향 확인** | 바꾸려는 함수가 어디에 영향을 주는지(호출자·구현체·진입점·테스트), 코어를 건드리지 않고 끼울 수 있는 확장 포인트가 어디인지 보여 줍니다. |
| **원본 업데이트 따라가기** | 두 버전을 비교해 우리 수정과 부딪히는 곳(함수 삭제·이름 변경·시그니처 변경)을 찾습니다. |
| **기능만 골라 옮기기** | 진입점 하나에서 필요한 코드·상태·설정·외부 의존을 잘라 내고, 항목마다 어디로 옮겼는지 원장에 기록합니다. 다른 언어로 다시 만들어도 됩니다. |
| **옮긴 결과 검증** | 같은 입력을 원본과 내 코드에 넣어 결과를 비교합니다(HTTP·CLI·함수 단위). 일부러 바꾼 동작은 기대값과, 원본에 없는 새 동작도 기대값으로 검증합니다. |
| **원하는 기능 만들기** | 참고할 원본 없이 "이런 기능을 만들어 줘" 에서 시작해, 요구사항과 합격 기준을 먼저 승인받고 만든 뒤 기준대로 됐는지·이상한 입력에도 버티는지 검증합니다. |
| **AI 지름길 차단** | 차이 허용·기능 생략·테스트 축소·기대값은 사람 승인 전까지 완료가 안 됩니다. 검증 뒤 코드가 바뀌거나 기록을 툴킷 밖에서 고치면 잡아냅니다. |

## 핵심 개념

**근거 등급** — 결론이 얼마나 확실한지를 등급으로 남깁니다.

| 등급 | 뜻 | 얻는 방법 |
|---|---|---|
| L0 | 코드를 읽고 구조 지도로 확인 | 기본 |
| L1 | 언어 서버로 호출·참조 확인 | `lsp` |
| L2 | 값이 어디서 어디로 흐르는지 확인 | `dataflow` |
| L3 | 실제로 실행되는 것을 확인 | 커버리지 리포트, 차등 검증 |

**원장** — 분석·포팅 상태는 프로젝트별 SQLite 원장(`~/.oss-analyzer/projects/<id>/ledger.db`)에 쌓입니다.
세션이 바뀌어도, 여러 에이전트가 동시에 일해도 같은 기록을 이어 씁니다.

**포팅 완료 조건 (게이트 7개)** — 전부 충족해야 `port-finish` 가 완료 판정을 냅니다.

1. 옮길 항목이 모두 처리됨 (옮김 / 대체 / 생략)
2. 옮긴 위치가 지금도 그대로 있음 (줄 내용까지)
3. 차등 검증 통과 — 마지막으로 저장한 케이스 전부, **지금 코드 기준** (검증 뒤 코드가 바뀌면 무효)
4. 케이스가 옮길 함수를 모두 실행함 (원본 커버리지, 안 되면 내 코드 커버리지)
5. 사람 확인 항목이 모두 승인됨 (차이 허용·생략·케이스 변경·기대값, 승인 뒤 내용이 바뀌면 무효)
6. 기록이 툴킷 밖에서 바뀌지 않음 (원장 해시 사슬·골든 해시)
7. 라이선스 확인이 필요한 파일이 없음

모든 포팅 명령 응답에는 `next_step`(다음에 할 일)이 붙습니다.

## 요구 사항

| 구분 | 내용 |
|---|---|
| 필수 | Python 3.11+, git |
| 필수 엔진 | 구조 지도 엔진, 진입점 규칙 엔진 — `setup` 이 설치 (체크섬 검증) |
| 선택 | 언어 서버(L1), 데이터 흐름 엔진(L2, JDK 21+), 커버리지 도구(L3), 컨테이너 엔진(남의 코드 격리 실행) |
| 언어 서버(L1) | Rust · Java · Kotlin · TypeScript/JavaScript · Python · Go · C/C++ · C# · Dart · Ruby (그 밖의 언어는 L0 까지, 파일·심볼 분류는 언어와 무관) |
| 진입점 규칙 | Axum · Spring · Express · FastAPI/Flask · Gin · clap · Next.js (규칙이 없는 프레임워크는 자동으로 미확인 처리) |

엔진 이름·라이선스·설치 위치는 [THIRD_PARTY.md](THIRD_PARTY.md) 에 있습니다.

## 설치

```bash
# 1. 툴킷 설치 — 어느 폴더에서나 oss-analyzer 명령으로 쓸 수 있게
pipx install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git
# (또는) uv tool install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git

# 2. 상태 점검 — 엔진·언어 서버·스킬·MCP 등록
oss-analyzer doctor

# 3. 엔진 설치 — 먼저 계획만 보고, 고른 것만 설치
oss-analyzer setup --languages python typescript
oss-analyzer setup --apply codebase-memory-mcp ast-grep lsp:python lsp:typescript coverage
```

- `setup` 은 아무 옵션 없이 실행하면 **계획만** 보여 주고 아무것도 바꾸지 않습니다. `--apply` 에 적은 것만 설치합니다.
- 남의 코드를 격리해서 실행하려면 컨테이너 엔진이 필요합니다. Windows 는 `setup --apply sandbox` 가 WSL 배포판 안에
  컨테이너 엔진을 설치합니다 (데스크톱 앱 불필요). 이미 `docker` 가 있으면 그것을 씁니다.
- JDK·Node.js·Go·Rust 같은 언어 런타임은 직접 설치합니다.

### AI 에이전트(Claude Code) 연동

둘 중 하나만 씁니다.

```bash
# A. setup 으로 스킬 + MCP 등록
oss-analyzer setup --apply skill mcp-register
oss-analyzer setup --apply skill mcp-register --scope project --project-dir <프로젝트>   # 한 프로젝트에서만

# B. Claude Code 플러그인 (툴킷은 위 1번으로 먼저 설치)
claude plugin marketplace add ParkBeomsoo/beom-oss-analyzer
claude plugin install beom-oss-analyzer@beom-oss-analyzer
```

- 스킬은 설치한 사본에 버전을 남겨, 툴킷이 업데이트되면 `doctor` 가 `outdated` 로 알려 줍니다.
- 같은 이름의 스킬을 직접 만들어 두었거나 다른 설치를 가리키는 MCP 등록이 있으면 덮어쓰지 않고 알려 줍니다.
- MCP 없이 CLI 만 써도 됩니다. 승인·완료 판정 같은 규칙은 CLI 에서도 똑같이 동작합니다.

### 업데이트와 제거

```bash
pipx upgrade beom-oss-analyzer
oss-analyzer setup --update                      # 이미 설치한 스킬만 새 버전으로 (새로 설치하지 않음)
oss-analyzer setup --remove skill mcp-register   # 이 툴킷이 설치·등록한 것만 되돌림
pipx uninstall beom-oss-analyzer
```

## 데스크톱 앱과 자동 실행

```bash
pipx install "beom-oss-analyzer[app] @ git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git"
beom-oss-analyzer                    # 앱 열기
oss-analyzer app --shortcut          # 시작 메뉴(검색) + 바탕화면 바로가기 만들기, --remove-shortcut 으로 지움
```

앱을 처음 열 때도 바로가기를 만들지 물어봅니다. Linux 는 `.desktop` 항목을 만듭니다.

**홈에서 모드 고르기**

| 모드 | 이럴 때 | 진행 단계 |
|---|---|---|
| **요청사항 기반** | 이런 기능을 만들어 줘 (참고할 원본 없음) | 요구사항 → 합격 기준 → 계획 → 구현 → 검증 → 인수인계 |
| **오픈소스 기반** | 이 저장소의 기능을 내 프로젝트로 | 분석 → 범위 → 원본 기록 → 계획 → 구현 → 원본과 비교 → 인수인계 |

오픈소스 모드는 저장소 URL(또는 경로)만 필수입니다. 가져올 기능은 입력창에 말로 적거나, 비워 두면 에이전트가 기능 목록을 보여 주고 고르게 합니다.

**에이전트 · 모델 · 노력** — 보내기 버튼 옆 버튼 하나에서 고릅니다.

| 에이전트 | 참고 |
|---|---|
| Claude Code | 작업마다 툴킷 권한 규칙으로 확인. 모델: 기본 · Opus · Sonnet · Haiku (별칭이라 항상 최신), 노력 낮음 → 최대 |
| Codex | 작업별 질문 대신 샌드박스로 막음 (대상 폴더 안에서만 쓰기). 모델·노력 단계는 Codex 가 계정 기준으로 받아 둔 목록을 그대로 써서 새 모델이 자동으로 나옴 |
| Gemini CLI | 실험적, 노력 설정 없음 |

목록에 없는 모델 이름은 직접 입력합니다. 모델과 노력은 진행 중에도 바꿀 수 있습니다 (다음 라운드부터).

**권한** — 모드를 고르고, 종류별 세부 규칙은 *옵션 → 권한* 에 있습니다.

| 모드 | 동작 |
|---|---|
| 모두 물어보기 | 수정·명령마다 확인 |
| 편집 자동 허용 (기본) | 대상 폴더 안 수정과 빌드·테스트는 바로, 그 밖의 명령과 웹은 확인 |
| 자동 | 명령도 묻지 않고 실행, 웹과 폴더 밖 파일만 확인 — 믿을 수 있는 작업에만 |

push·배포·폴더 밖 삭제·시스템 변경은 언제나 거절합니다. 에이전트가 검증 도구에 넘기는 명령(케이스 파일 안의 명령 포함)도 같은 규칙을 거칩니다.
인증: *내 로그인*(구독) 또는 *API 키*(환경 변수의 키로 종량 과금) — 할 수 있는 일은 같습니다.

**사람이 정하는 것** — 이것만 카드로 뜹니다.

- **요구사항과 합격 기준**, 그다음 **계획**: *승인*, *고치기*(요구사항·케이스·계획 단계를 직접 고친 뒤 *저장하고 승인* — 원장에 사용자 작성으로 남고
  에이전트에게도 전달), 또는 입력창에 고칠 점을 적기 (승인 없이 에이전트가 고침).
- 에이전트의 **질문**과 **권한 요청** (`Y` 허용 · `N` 거절).
- 마지막 **인수인계**: 수락하거나, 메시지를 보내 계속 — 멈췄거나 끝난 실행에 메시지를 보내면 이어서 진행합니다.

승인은 앱 안에서만, 화면에 보인 내용 그대로에 묶여 원장에 기록됩니다. 에이전트가 닿을 수 있는 승인 경로는 없습니다.

**오른쪽 패널**

| 탭 | 보이는 것 |
|---|---|
| 현황 | 진행률, 계획 단계, 요구사항, 완료 조건 |
| 기준 | 합의한 것 — 요구사항, 합격 케이스(입력 → 기대), 계획, 누가 언제 승인했는지 |
| 검증 | 검증한 것 — 요구사항별 케이스의 입력 → 기대 → 실제 결과, 완료 조건마다 무슨 뜻인지 |
| 변경 | 바뀐 파일과 diff, 파일별 되돌리기, 체크포인트 (라운드마다 대상 폴더를 기록해 아무 시점으로 되돌림) |

**라운드** — 에이전트 세션 한 번이 한 라운드입니다. 보통 요청 하나에 4~6라운드(질문·수정이 있으면 더)이고 기본 상한은 8입니다.
두 라운드 연속 진행이 없으면 스스로 멈추고, 언제든 메시지로 이어 갈 수 있으니 상한은 비용 안전장치일 뿐입니다.

**데이터 저장 위치** — 모두 내 컴퓨터에 남습니다. [데이터와 저장 위치](#데이터와-저장-위치)를 보세요.

앱 없이: `oss-analyzer run --mode request --target-root <폴더> --request "<요청>"` (`--agent codex`, `--resume <실행>`), 답은 앱에서 합니다.

Claude Code 는 [공식 안내](https://code.claude.com/docs)대로 설치하고 `claude auth login`, Codex 는 `npm i -g @openai/codex` 후 `codex login`
(확인: `oss-analyzer agent-status`). 내 컴퓨터에서 본인이 쓰면 구독 로그인, 다른 사람에게 서비스하려면 API 키를 씁니다
([Anthropic 약관](https://code.claude.com/docs/en/legal-and-compliance)).

## 빠른 시작 1: 저장소 전수 분석

**AI 에이전트에게 맡길 때** — Claude Code 에서:

> 이 저장소 전수 분석해줘 https://github.com/<조직>/<저장소>

스킬이 아래 절차를 따라 진행하고, 큰 저장소는 서브에이전트 여러 개가 단위를 나눠 병렬로 분석합니다.

**CLI 로 직접 할 때**

```bash
oss-analyzer open https://github.com/<조직>/<저장소> --ref v1.2.0   # 분석 전용 clone, 커밋 고정 → project_id
oss-analyzer inventory <pid>                    # 파일 분류·심볼·구조 지도·진입점·분석 단위
oss-analyzer features <pid>                     # 실제 URL·CLI 진입점 목록

# 분석 루프 (단위가 없을 때까지 반복)
oss-analyzer next <pid> --agent a1              # 다음 단위 받기
oss-analyzer context <pid> <unit_id> --agent a1 --raw
oss-analyzer finding <pid> <unit_id> --agent a1 --json finding.json   # 결론 + 근거
oss-analyzer unknown-add <pid> --agent a1 --json unknown.json        # 확정 못 한 것
oss-analyzer complete <pid> <unit_id> --agent a1 --summary "이 단위가 하는 일"

oss-analyzer coverage <pid>                     # 파일·심볼 2축 완료율과 실패 항목
oss-analyzer unknowns <pid>                     # 남은 미확인
oss-analyzer export <pid>                       # Markdown + JSON 리포트
```

`finding.json` 예:

```json
{"text": "주문 생성 시 재고를 먼저 잠그고 결제 실패 시 해제한다", "severity": "important",
 "evidence": [{"path": "src/order/service.rs", "start_line": 120, "end_line": 158}],
 "symbol_ids": ["s_1a2b3c"]}
```

## 빠른 시작 2: 기능 옮기기와 검증

예: 원본의 `POST /api/v1/orders` 를 내 저장소에 다른 언어로 옮기는 경우.

```bash
# 1. 옮길 범위 보기 → 포팅 원장 만들기
oss-analyzer slice <pid> "POST /api/v1/orders"
oss-analyzer port-create <pid> --name 주문 --targets "POST /api/v1/orders" --language javascript --target-root <내 저장소>

# 2. 원본 응답 저장 (골든) — 원본은 컨테이너에서 격리 실행
oss-analyzer port-capture <pid> <port_id> --cases cases.json \
    --source-image <원본 실행 이미지> --source-url http://127.0.0.1:18001 --source-cmd "<원본 서버 실행 명령>" \
    --coverage-cmd "<커버리지 리포트 생성 명령>" --coverage-report cov.json --explore 40

# 3. 내 코드 작성 → 항목마다 어디로 옮겼는지 기록
oss-analyzer port-map <pid> <port_id> --json map.json

# 4. 비교 검증 (동시 요청까지)
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:18002 --target-cmd "node server.js" --concurrency 8

# 5. 진행 상황과 다음 할 일
oss-analyzer port-status <pid> <port_id>

# 6. 사람 승인 — 사용자가 터미널에서 직접 실행 (에이전트가 대신 실행하면 거부됨)
oss-analyzer port-approve <pid> <port_id>

# 7. 완료 판정 — 게이트 7개가 모두 충족될 때만 발급
oss-analyzer port-finish <pid> <port_id>
```

`map.json` 예 — 옮김(`ported`)은 내 코드의 `경로:줄`, 대체·생략은 사유가 필요합니다.

```json
[{"item": "s_1a2b3c", "status": "ported", "target": "src/orders/create.js:12"},
 {"item": "s_4d5e6f", "status": "replaced", "note": "원본 큐 대신 기존 메시지 서비스 사용"},
 {"item": "s_7a8b9c", "status": "omitted", "note": "관리자 통계는 이번 범위 밖"}]
```

**옮긴 기능을 바꿔서 가져오기**

| 상황 | 방법 | 판정 |
|---|---|---|
| 원본과 같아야 함 | 케이스만 작성 | 원본 응답과 비교 |
| 일부러 다르게 만듦 (값을 정할 수 있음) | 케이스에 `expect` | 원본 대신 기대값과 비교, 기대값은 사람 승인 |
| 원본에 없는 새 동작 | `"new": true` + `expect` | 원본은 실행하지 않고 기대값으로 판정 |
| 형식만 다르고 값은 못 정함 | `port-accept --case <이름> --reason "<사유>"` | 차이 허용 제안 → 사람 승인 |
| 기능 일부를 뺌 | `port-map` 에서 `omitted` + 사유 | 사람 승인 |

- 승인 화면에는 원본 응답과 기대값(또는 실제 차이)이 나란히 나옵니다. 승인 뒤 내용이 바뀌면 다시 승인해야 합니다.
- 원본을 커버리지로 계측할 수 없으면 `port-verify --target-coverage-cmd ... --target-coverage-report lcov.info` 로 내 코드 쪽 실행 범위를 잽니다.
- 내 코드를 고쳐 줄이 밀리면 `port-map --follow` 로 옮긴 위치를 갱신합니다.

## 빠른 시작 3: 요청사항으로 만들기

앱에서 **요청사항 기반** 을 고르거나, CLI 로:

```bash
oss-analyzer make-create --name 주문총액 --request "수량을 받아 총액 계산. 개당 2000원, 10개 이상 10% 할인, 0 이하면 400" --target-root <폴더>
oss-analyzer make-req <pid> <port_id> --json reqs.json          # [{"text": "총액 = 수량 x 2000원"}, …] → R1, R2 …
oss-analyzer port-capture <pid> <port_id> --cases cases.json --explore 20   # 요구사항마다 expect + req
oss-analyzer port-approve <pid> <port_id>                       # 구현 전에 요구사항·합격 기준 승인
oss-analyzer port-map <pid> <port_id> --json map.json           # 요구사항 → 구현 위치 "경로:줄"
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:8080 --target-cmd "<서버 실행 명령>"
oss-analyzer port-finish <pid> <port_id>
```

- 케이스마다 `req`(확인하는 요구사항)와 `expect`(기대값)가 필요하고, 요구사항마다 케이스가 하나 이상 있어야 합니다.
- 입력 자동 변형 케이스는 기대값 대신 **죽지 않음·5xx 아님** 으로 판정합니다 (이상한 입력은 4xx 로 거절해야 통과).
- 넣을 저장소가 있으면 먼저 `open`·`inventory` 해서 `--context <그 project_id>` 로 넘기면 기존 구조에 맞춰 만듭니다.

## 원본을 고쳐 쓰는 경우 (커스텀)

```bash
oss-analyzer extpoints <pid>                                  # 코어를 안 건드리고 끼울 수 있는 곳 먼저
oss-analyzer impact <pid> pricing.calc_total --verify         # 바꾸면 영향받는 진입점·테스트·결론
oss-analyzer custom-add <pid> --description "할인 계산 변경" --kind modify --targets pricing.calc_total

# 원본 새 버전이 나오면
oss-analyzer open <저장소> --ref v1.3.0 && oss-analyzer inventory <새 pid>
oss-analyzer compare <기준 pid> <새 pid> --save                # 바뀐 곳 + 우리 수정과 부딪히는 곳
```

고친 원본을 원래 원본과 비교 검증하려면, 고친 저장소를 `--target-root` 로 두고 [빠른 시작 2](#빠른-시작-2-기능-옮기기와-검증) 와 같은 방법으로 검증합니다.

## 케이스 파일 형식

```json
{
  "defaults": {"headers": {"Content-Type": "application/json"}},
  "cases": [
    {"name": "주문생성", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 2},
     "match": {"id": "[0-9a-f]{32}"}, "ignore": ["createdAt"]},
    {"name": "주문조회", "method": "GET", "path": "/api/v1/orders/{{주문생성.id}}"},
    {"name": "수량0", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 0}, "compare": "status"},
    {"name": "할인적용", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 10},
     "expect": {"status": 201, "equals": {"total": 18000}}},
    {"name": "쿠폰_새기능", "new": true, "method": "POST", "path": "/api/v1/orders",
     "json": {"sku": "A1", "qty": 1, "coupon": "WELCOME"}, "expect": {"equals": {"discount": 1000}}}
  ]
}
```

| 필드 | 뜻 |
|---|---|
| `method`·`path`·`json`·`headers` | HTTP 케이스 |
| `argv`·`stdin` | CLI 케이스 |
| `call: {source, target}`·`args`·`kwargs` | 함수 단위 비교 (`경로:함수명`, 언어가 달라도 됨) |
| `{{케이스이름.필드}}` | 앞 케이스 결과 값 사용 (이름에 공백 없이) |
| `ignore` | 비교하지 않을 경로 (`items.*.t`) |
| `match` | 값 대신 정규식으로 확인 (id·시간처럼 매번 다른 값) |
| `compare: "status"` | 상태 코드만 비교 |
| `expect` | 원본 대신 기대값과 비교 — `status`·`body`·`equals`(경로: 값, 배열은 `items.0.price`)·`match` |
| `new: true` | 원본에 없는 동작 (`expect` 필수, 원본에서는 실행 안 함) |
| `explore: false`·`concurrent: false` | 입력 자동 변형·동시 요청 검사에서 제외 |

표에 없는 키는 저장할 때 거절합니다 (`argv` 대신 `args` 처럼 잘못 쓴 키가 조용히 무시되면 다른 입력으로 검증되기 때문). 요청사항 모드는 케이스마다 `req` 도 필요합니다.

## 명령 요약

| 분류 | 명령 |
|---|---|
| 준비 | `doctor` `setup` `open` `list` `inventory` `classify` `agent-status` |
| 앱·자동 실행 | `app` `run` `runs` `data` |
| 분석 루프 | `next` `claim` `renew` `context` `finding` `withdraw` `mark` `unknown-add` `unknown-update` `complete` `review` `pending` |
| 정밀 확인 | `lsp`(L1) `dataflow`(L2) `runtime-ingest` `runtime-run` `runtime-runs`(L3) |
| 결과 | `coverage` `features` `units` `unknowns` `export` `license` |
| 커스텀 | `extpoints` `impact` `custom-add` `custom-list` `custom-update` `compare` |
| 요청사항 만들기 | `make-create` `make-req` (이후 port-* 명령 공통) |
| 포팅 | `slice` `port-create` `port-map` `port-capture` `port-verify` `port-accept` `port-status` `port-approve` `port-finish` |

각 명령의 옵션은 `oss-analyzer <명령> --help`. 모든 명령은 `{ok, data, warnings, errors}` JSON 을 출력하고,
실패하면 `errors[0].recovery` 에 해결 방법이 나옵니다. 에이전트용 상세 절차는 스킬 문서(`oss-analyze` 스킬의 SKILL.md·CLI.md)에 있습니다.

## 데이터와 저장 위치

Claude Code(`~/.claude`)·Codex(`~/.codex`)처럼 홈 아래 폴더 하나(`~/.oss-analyzer`, `OSS_ANALYZER_HOME` 으로 변경)에 모으고,
안은 성격별로 나눕니다. 어디로도 보내지 않고, 비밀은 저장하지 않습니다 — 키·토큰은 환경 변수로만 읽고 출력에서는 가립니다.

| 폴더 | 내용 | 지워도 되나 |
|---|---|---|
| `settings.json` | 사용자 설정 (정리 주기, 기본값) | 됨 — 기본값으로 |
| `projects/<id>/` | 분석·포팅 원장 `ledger.db`(SQLite, 스키마 버전, 해시 사슬), 고정 커밋의 원본 클론, 골든 응답 | **안 됨** — 승인·검증 기록 |
| `runs/<실행>/` | 자동 실행 하나 = 대화 하나: `run.json` 상태, `events.jsonl` 흐름(추가만), `inbox/` 질문·승인 요청, `shadow.git` 대상 폴더 체크포인트, `agent/` 라운드마다 다시 만드는 에이전트 설정 | 휴지통으로 |
| `trash/` | 지운 실행, 7일 뒤 자동으로 비움 | 됨 |
| `app/` | 바로가기가 가리키는 앱 아이콘 | 안 됨 |
| `cache/` · `cbm-cache/` | 에이전트 상태, 검증 하네스, 구조 지도 색인 | 됨 — 다시 만듦 |
| `work/` | 검증·커버리지용 임시 사본, 쓰고 바로 지움 | 됨 |
| `bin/` · `engines/` · `node/` | `setup` 이 설치한 분석 엔진 | 됨 — `setup` 으로 다시 설치 |

에이전트와의 대화 원본은 각 CLI 가 원래 두는 곳(`~/.claude/projects`, `~/.codex/sessions`)에 있고 세션 id 로 이어 갑니다.
대상 폴더에는 `.oss/cases.json`(합격 케이스)만 씁니다 — 커밋하든 무시하든 선택입니다.

| | Claude Code | Codex | 이 툴킷 |
|---|---|---|---|
| 대화 | `projects/<폴더>/<세션>.jsonl` | `sessions/YYYY/MM/DD/*.jsonl` | `runs/<실행>/events.jsonl` (+ 에이전트 자체 대화 기록) |
| 설정 | `settings.json` | `config.toml` | `settings.json` |
| 상태 | 파일 변경 기록(체크포인트) | 버전 붙은 SQLite | 프로젝트별 SQLite 원장(스키마 버전, 해시 사슬) + 실행별 git 체크포인트 |
| 정리 | 기본 30일 지난 대화 자동 삭제 | 보관 | 기본 보관, 원하면 끝났거나 숨긴 실행을 30/90일 뒤 자동 정리, 휴지통은 7일 뒤 비움 |

실행을 지워도 승인·검증 결과는 지워지지 않습니다 — 프로젝트 원장에 남습니다.

```bash
oss-analyzer data                        # 어디에 무엇이 얼마나
oss-analyzer data --trash <실행>          # 실행을 휴지통으로 (--restore <이름>, --empty-trash)
oss-analyzer data --prune 30 --dry-run   # 30일 동안 그대로인 끝났거나 숨긴 실행
oss-analyzer data --set-cleanup 30       # 시작할 때 자동 정리 (0 = 끔)
```

앱에서는 사이드바 아래 **저장 공간**, 실행의 ⋯ 메뉴 **휴지통으로**.

## 설정

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `OSS_ANALYZER_HOME` | `~/.oss-analyzer` | 원장·clone·엔진·백업 위치 |
| `OSS_ANALYZER_CONTAINER` | `auto` | 격리 실행 엔진 `auto`·`docker`·`wsl`·`none` |
| `OSS_ANALYZER_WSL_DISTRO` | `oss-sandbox` | Windows 에서 쓸 WSL 배포판 |
| `OSS_ANALYZER_LEASE_SECONDS` | `3600` | 분석 단위 임대 시간 |
| `OSS_ANALYZER_UNIT_MAX_LINES` / `_UNIT_MAX_CHARS` | `1500` / `45000` | 분석 단위 크기 상한 |
| `OSS_ANALYZER_CONTEXT_MAX_CHARS` | `24000` | `context` 한 페이지 크기 |
| `OSS_ANALYZER_LSP_TIMEOUT_SECONDS` / `_LSP_IDLE_SECONDS` | `900` / `600` | 언어 서버 응답 대기·유휴 종료 |
| `OSS_ANALYZER_RULEPACKS` | — | 추가 진입점 규칙 폴더 |
| `OSS_ANALYZER_CBM` · `_ASTGREP` · `_JOERN` · `_JAVA_HOME` | 자동 탐색 | 엔진 실행 파일 직접 지정 |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | 스킬을 설치할 Claude Code 설정 폴더 |

## 안전 원칙

- **분석 대상 코드는 실행하지 않습니다.** 언어 서버의 빌드 스크립트·매크로 실행도 끕니다. 실행이 필요한 검증(커버리지·포팅 원본)은
  컨테이너(네트워크 차단)에서, 또는 사용자가 신뢰한다고 한 저장소만 임시 사본에서 실행합니다.
- **비밀 파일은 읽지 않고**, 일반 파일 속 비밀값(비밀번호·키·토큰·URL 자격 증명)은 출력할 때 `****` 로 가립니다.
  실행 환경 변수에서도 토큰·키류는 빼고 넘깁니다.
- 분석은 **전용 clone 의 고정 커밋**에서만 합니다. 작업 중인 원본 폴더의 미커밋 변경은 반영되지 않습니다.
- 포팅의 사람 승인은 데스크톱 앱, MCP 확인 창, 사용자의 터미널 입력으로만 받습니다 — 자동 실행 중인 에이전트에게서는 받지 않습니다.

## 알려진 한계

| 한계 | 대응 |
|---|---|
| 차등 검증은 돌려 본 입력만 증명 | 입력 자동 변형·커버리지로 범위를 넓히고, 남은 범위는 근거의 limitations 에 기록. 성능은 비교하지 않음 |
| 커버리지는 "실행됐다"만 증명 | 어떤 분기·입력인지는 케이스로 따로 확인 |
| 이름 기반 호출 관계는 후보 | 언어 서버(L1)나 실행(L3)으로 확인해야 근거 |
| 동적 디스패치·리플렉션·매크로 생성 코드 | 자동으로 미확인 처리 |
| 데이터 흐름 엔진 첫 실행이 느림 (1만 줄 Java 약 15분) | 캐시, `dataflow --build-only` 로 미리 생성 |
| CLI 는 호출마다 언어 서버를 새로 띄움 | 같은 언어는 프로세스 간 줄 세움. MCP 모드는 서버를 재사용 |
| 설정값에 따라 바뀌는 경로 | 기본값으로 펼치고 미확인으로 표시 |
| 로컬 프로그램이라 같은 권한으로 작정하고 기록을 위조하면 막을 수 없음 | 위조 시도는 게이트 실패로 드러남. 최종 판정은 깨끗한 환경에서 다시 실행 가능 |

## 라이선스

**사용 허용, 수정 금지** — [LICENSE](LICENSE) (오픈소스 라이선스가 아닙니다).

- 수정하지 않은 상태로 설치·사용하는 것은 누구나 가능합니다 (회사 업무·상업적 목적 포함). 툴킷으로 만든 결과물은 사용자의 것입니다.
- 수정·2차 저작물·수정본 배포·코드 일부를 다른 소프트웨어에 넣는 것은 저작자의 서면 허락이 필요합니다.
- 개선 제안은 이 저장소의 이슈·풀 리퀘스트로 받습니다.

사용하는 라이브러리·외부 엔진은 각자의 라이선스를 따릅니다 — [THIRD_PARTY.md](THIRD_PARTY.md).
