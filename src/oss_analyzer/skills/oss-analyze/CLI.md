# oss-analyzer CLI 로 단위 분석하기 (서브에이전트·배치용)

MCP 도구와 같은 기능을 CLI 로 쓴다. 절차·판단 규칙은 SKILL.md 와 동일하다.
모든 명령은 UTF-8 JSON envelope `{ok, data, warnings, errors}` 를 출력한다(`context --raw` 제외).
`ok=false` 면 `errors[0].recovery` 를 따른다. 오류도 항상 envelope 로 나온다.
Windows 에서 출력을 파이썬으로 읽을 때는 `encoding="utf-8"` (또는 `python -X utf8`).

```
OA=oss-analyzer   # PATH 의 명령 (설치 위치는 사람마다 다르다 — 경로를 짐작하거나 적어 두지 않는다)
```

명령이 없으면 사용자에게 설치를 요청한다 (README 의 설치). `oss-analyzer doctor` 의 `integration` 에
CLI·스킬·MCP 등록 상태가 나온다.

## 루프

```bash
$OA next <pid> --agent <내 id>                 # 다음 단위 임대 (data.unit 이 null 이면 끝)
$OA claim <pid> <unit_id> --agent <내 id>      # 특정 단위 임대
$OA renew <pid> <unit_id> --agent <내 id>      # 임대 연장 (도구 호출마다 자동 연장됨)
$OA context <pid> <unit_id> --agent <내 id> --raw            # 사람이 읽는 텍스트 (권장)
$OA context <pid> <unit_id> --agent <내 id> [--offset N]     # JSON
$OA finding <pid> <unit_id> --agent <내 id> --json <파일>   # 결론 (객체 1개 또는 배열)
$OA mark <pid> <unit_id> --agent <내 id> --note "<사유>" --symbols s_a s_b ...
$OA unknown-add <pid> --agent <내 id> --json <파일>          # 미확인 (객체 1개 또는 배열)
$OA lsp <pid> --json <파일>                                  # LSP 질의 배치 (L1)
$OA dataflow <pid> --json <파일>                             # 데이터 흐름 질의 (L2, Joern)
$OA complete <pid> <unit_id> --agent <내 id> --summary "<단위 요약>"
```

### context
- 한 페이지는 약 24KB. `next_offset` 이 있으면 `--offset <next_offset>` 으로 이어 읽는다.
  offset 은 **단위 안의 줄 순번**이고, `next_line` 이 다음 페이지가 시작하는 `파일:줄` 이다.
- `symbols`·`calls` 는 첫 페이지에만 나온다. `symbols[].name` 이 짧은 이름.
- `calls` 는 구조 지도의 이름 기반 간선 = **항상 L0** (strategy 이름에 lsp 가 들어가도). 신뢰도 0.5 미만은 숨김(`--all-calls` 로 보기).
  `call_line_is_declaration=true` 면 위치가 호출 줄이 아니라 함수 선언 줄이다.
  `strategy=mybatis_namespace` 는 Java 매퍼 메서드 → MyBatis XML 구문 연결.
- `****` 는 비밀값 가림. 값을 추측하거나 옮겨 적지 않는다.

### finding JSON
```json
{"text": "결론 문장", "severity": "info|important|critical",
 "evidence": [{"path": "a/b.rs", "start_line": 10, "end_line": 20}, {"evidence_id": "ev_..."}],
 "symbol_ids": ["s_..."]}
```
같은 단위에 **완전히 같은** `text` 를 다시 내면 그 결론을 덮어쓴다(근거 보강용).
**문장을 한 글자라도 고쳐 다시 낼 때는 `"replaces": "<옛 claim_id>"`** 를 넣어야 옛 결론이 철회된다 (안 넣으면 중복).
이미 낸 결론이 틀렸으면 `$OA withdraw <pid> <claim_id> --agent <내 id> --reason "<사유>"` (완료된 단위도 가능).
배열로 넣으면 응답은 항상 `{"results": [...]}`, 객체 하나면 결과 객체 하나.

### unknown-add JSON
```json
{"question": "...", "domain": "dataflow|dynamic-dispatch|config|external-dependency|entrypoint|capability|security|...",
 "severity": "low|medium|high|critical", "required_level": "L1", "scope": "<path:line 등 중복 방지 키>"}
```
`created:false` 는 같은 영역·질문·범위가 이미 있다는 뜻(`reason` 참고).

### lsp JSON (줄·열 1부터, 함수·심볼 **이름** 위치)
```json
[{"op": "incoming|outgoing|references|definition", "path": "a/b.rs", "line": 37, "column": 14}]
```
- 결과의 `evidence_id`(L1)를 finding 의 evidence 에 넣으면 그 결론이 L1 이 된다.
- 같은 언어 서버는 프로세스 간 줄을 서서 하나씩 뜬다(대기 중이면 stderr 에 표시). 질의는 단위당 1배치로 모은다.
- 질의 하나가 실패해도 나머지 결과는 나온다. `count=0` 은 '없음'의 증명이 아니다(`note` 참고).

### dataflow JSON (L2)
```json
[{"path": "a/Controller.java", "line": 91, "sink": "select.*|insert.*|update.*", "max_flows": 20}]
```
`line` 이 함수 안이면 그 함수의 매개변수, 호출 줄이면 그 호출의 인자가 출발점. 처음 한 번 CPG 생성이 오래 걸린다(`--build-only`).

## 등급 규칙
- 결론 등급 = 인용한 근거 중 **가장 높은** 등급. 직접 읽은 위치·calls = L0, lsp = L1, dataflow = L2.
- 단위 등급 = 중요(important/critical) 결론 등급의 최솟값. 중요 결론이 없으면 결론 등급의 최댓값.
- 코드 단위 목표는 L1, 설정·자산 단위는 L0. **중요 결론**이 목표 미달이면 complete 가 '검증 공백' 미확인 1건(low)을 남긴다. info 결론은 L0 이어도 공백이 아니다.

## 규칙 요약
- 결론마다 근거 필수. 위치는 context 출력의 줄 번호 그대로.
- `symbols` 전부를 finding 의 symbol_ids 에 넣거나 mark 로 처리해야 complete 가 된다.
  MyBatis XML 은 SQL 구문(SqlStatement/SqlFragment)이 심볼이다.
- 호출 관계를 중요 결론으로 쓰면 lsp 근거로, 값이 어디로 흘러가는지(권한·금액·삭제 조건)는 dataflow 근거로.
- 확신 없는 것, 동적 동작, 설정·환경 의존, 외부 라이브러리 의존 → unknown-add.
- 분석 대상 코드를 빌드·실행하지 않는다. `.env*` 를 읽지 않는다.

## 커스텀·실행 검증·라이선스

```bash
oss-analyzer extpoints <pid> [--kind interface|registry|middleware|config-key]
oss-analyzer impact <pid> <대상...> [--verify] [--depth 4] [--no-name-match]   # 대상: s_id / 경로:줄 / 파일 / 이름
oss-analyzer custom-add <pid> --description "..." --kind extend|modify|replace|config|remove [--targets ...] [--extension-point xp_...]
oss-analyzer custom-list <pid> [--status planned|applied|dropped]
oss-analyzer custom-update <pid> <cus_id> --status applied --note "..."
oss-analyzer compare <기준 pid> <새 pid> [--save]
oss-analyzer runtime-ingest <pid> <리포트> [--root <실행 디렉터리>]
oss-analyzer runtime-run <pid> --report coverage.json --command "..." (--image <docker 이미지> | --trusted)
oss-analyzer license <pid>
oss-analyzer setup [--languages ...] [--apply <id...>]
```

## 포팅

```bash
oss-analyzer slice <pid> "POST /api/v1/orders" [심볼...]
oss-analyzer port-create <pid> --name ... --targets ... --language <대상 언어> --target-root <내 저장소>
oss-analyzer port-map <pid> <port_id> --json map.json      # [{item, status, target?, note?}]
oss-analyzer port-map <pid> <port_id> --follow             # 줄이 밀린 ported 대상 갱신
oss-analyzer port-capture <pid> <port_id> --cases cases.json (--source-url U [--source-cmd C] | --source-cli C) (--source-image I | --trusted) \
    [--explore N] [--coverage-cmd C --coverage-report 경로]
oss-analyzer port-verify <pid> <port_id> (--target-url U [--target-cmd C] | --target-cli C) [--concurrency N] [--cases ... --source-* 로 실시간] \
    [--target-coverage-cmd C --target-coverage-report 경로]   # 원본 계측이 안 될 때 대상 커버리지로 케이스 충분성
oss-analyzer port-accept <pid> <port_id> --case <이름 패턴*> --reason "..."   # 제안 — 사용자 승인 필요
oss-analyzer port-status <pid> [<port_id>]                   # gates · next_step · approvals · finished
oss-analyzer port-approve <pid> <port_id> [--list]          # 사용자가 터미널에서 직접 (파이프 실행은 human_required 로 거부)
oss-analyzer port-finish <pid> <port_id>                    # 게이트 전부 충족 시에만 판정 발급, 아니면 gates_unmet + next_step
```

에이전트는 `port-approve` 를 실행하지 않는다 (`--list` 로 대기 목록 보기만). 승인은 사람이 MCP 확인 창이나 터미널에서 직접.

케이스 JSON: `{"defaults": {...}, "cases": [{"name", "method", "path", "json"?, "headers"?, "ignore"?: [점 경로], "match"?: {점 경로: 정규식}, "compare"?: "full"|"status"} | {"name", "argv", "stdin"?} | {"name", "call": {"source": "app/x.py:fn", "target": "src/x.js:fn" 또는 {"cmd": "..."}}, "args"?, "kwargs"?, "runtime"?: {"source": "python 경로", "target": "node"}}]}`.
바꿔서 가져오는 경우 — 케이스에 `"expect": {status?, body?, equals?: {점 경로: 값}, match?: {점 경로: 정규식}}` 를 주면
원본 대신 이 기대값과 비교한다 (배열은 `items.0.price`). 원본에 없는 새 동작은 `"new": true` + `expect` (원본에서는 돌리지 않음).
기대값은 capture 때 원장에 기록되고 사용자 승인 대상이다. 기대값 케이스는 입력 변형을 만들지 않는다.
케이스에 `"explore": false` 면 변형 대상에서 빼고, `"concurrent": false` 면 동시 요청에서 뺀다. `match` 경로는 양쪽 다 없으면 같다고 본다(오류 응답).
앞 케이스 결과는 `{{이름.필드}}` 로 참조 (예: 생성한 주문 id 로 조회).
