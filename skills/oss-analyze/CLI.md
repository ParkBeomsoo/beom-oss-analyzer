# oss-analyzer CLI 로 단위 분석하기 (서브에이전트·배치용)

MCP 도구와 같은 기능을 CLI 로 쓴다. 절차·판단 규칙은 SKILL.md 와 동일하다.
모든 명령은 UTF-8 JSON envelope `{ok, data, warnings, errors}` 를 출력한다(`context --raw` 제외).
`ok=false` 면 `errors[0].recovery` 를 따른다. 오류도 항상 envelope 로 나온다.
Windows 에서 출력을 파이썬으로 읽을 때는 `encoding="utf-8"` (또는 `python -X utf8`).

```
OA=<설치 경로>/.venv/Scripts/oss-analyzer.exe   # Linux/macOS: <설치 경로>/.venv/bin/oss-analyzer
```

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
