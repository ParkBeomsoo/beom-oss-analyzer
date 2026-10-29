---
name: oss-analyze
description: >-
  오픈소스(또는 소스가 있는 저장소)를 단위별로 빠짐없이 전수 분석해서 기능 카탈로그·동작 명세·미확인 목록을
  근거(파일:줄)와 함께 만든다. oss-analyzer MCP 도구(oss_*)를 사용한다.
  "이 오픈소스 전부 분석해줘", "어떤 기능이 있는지 다 파악해줘", "커스텀하기 전에 구조 다 뜯어봐줘",
  "빠진 거 없이 분석", "전수 분석", "oss 분석" 같은 요청에 사용한다.
  대략적인 요약만 필요하면 이 스킬 대신 그냥 읽고 답한다. 소스 없는 바이너리 분석은 REA 같은 도구를 쓴다.
---

# 오픈소스 전수 분석 절차

목표: **숨은 빈틈 0** — 모든 파일이 분류되고, 모든 심볼이 검토되고, 확정 못 한 것은 전부 미확인으로 남는다.

## 0. 준비

1. `oss_doctor(languages=[...])` — `ready=false` 면 `install_hints` 를 사용자에게 보여주고 멈춘다.
2. `oss_repo_open(source, ref?)` — 커밋이 고정된 분석 전용 clone 이 만들어진다. 이후 모든 근거는 이 커밋 기준.
3. `oss_inventory_build(project_id)` — 결과의 `by_category.unclassified`, `missing_rulepacks`, `unknowns_open` 을 사용자에게 먼저 보고.
4. `unclassified` 파일이 있으면 내용을 보고 `oss_file_classify` 로 분류 (사유 필수). 분석 대상 범주로 바꿨으면 `rebuild=true`.

## 1. 단위 루프 (반복)

```
oss_unit_next(agent) → oss_unit_context(unit_id, agent) [next_offset 이 있으면 이어 읽기]
  → 코드를 읽고 단위가 하는 일을 결론으로 정리
  → oss_finding_record / oss_symbols_mark / oss_unknown_record
  → oss_unit_complete
```

결론 작성 규칙:
- **결론마다 근거 필수.** 직접 읽은 위치는 `{"path","start_line","end_line"}`, 도구 결과는 `{"evidence_id"}`.
- **호출 관계("A 가 B 를 부른다", "B 는 여기서만 쓰인다")** 는 `oss_lsp_call_hierarchy` / `oss_lsp_references` 의 evidence_id(L1)로 뒷받침한다.
  `oss_unit_context.calls` 의 간선은 이름 기반(L0)이라 단독 근거로는 약하다 (strategy 에 lsp 가 붙어도 L0).
- **값 흐름(요청값이 어떤 SQL·외부 호출까지 가는가, 권한·금액·삭제 조건)** 이 중요한 결론은 `oss_dataflow_query`(L2)로 확인한다.
- LSP 결과 0건은 '없음'의 증명이 아니다 — 0건을 근거로 "호출처 없음"이라고 결론 내지 말고 미확인으로.
- 등급 규칙: 결론 등급 = 인용 근거 중 최고 등급, 단위 등급 = 중요 결론의 최솟값. 중요 결론만 목표 등급(코드 L1)을 요구한다.
- 결론의 `symbol_ids` 에 설명한 심볼을 넣는다. 동작이 없는 심볼(단순 DTO·getter)은 `oss_symbols_mark` 로 사유와 함께 처리.
- 동작이 기능·보안·금전·데이터 손실에 영향을 주면 `severity="important"` 이상 → 나중에 독립 검토 대상.
- 다음은 **반드시 미확인으로** 남긴다 (`required_level` 은 해결에 필요한 근거 등급):
  - 동적 디스패치·리플렉션·DI·설정값에 따라 바뀌는 동작 → `L1` 또는 `L3`
  - 매크로·생성 코드·빌드 스크립트로 만들어지는 코드 (Rust proc-macro·derive 는 안전을 위해 LSP 에서 확장하지 않으므로 특히 주의)
  - 외부 라이브러리 동작에 결과가 좌우되는 부분 (`domain="external-dependency"`)
  - 값이 여러 함수를 거쳐 흐르는데 끝까지 못 따라간 경우 (`domain="dataflow"`, `L2`)
- 추측을 결론으로 쓰지 않는다. 확신이 없으면 미확인.
- 독립 검토에서 실제로 반려된 유형 — 이렇게 쓰지 않는다:
  - **예외를 빠뜨린 일반화**: "모든 라우트가 …" 라고 쓰기 전에 예외 경로·분기를 확인하고 결론에 넣는다.
  - **외부 라이브러리 동작 추정으로 결함 판정**: 저장소 밖 함수(공통 jar·crate·npm)가 던지는지/반환하는지 모르면
    결함으로 단정하지 말고 `domain="external-dependency"` 미확인으로 남긴다.
  - **정적 사실에서 런타임 동작으로 비약**: 환경변수·설정값·프레임워크 활성 여부에 달린 결과("쿠키가 유지되지 않는다")는
    조건을 명시하거나 미확인으로.
  - **의도 확인 없는 버그 단정**: 값이 이상해 보여도(TTL 30ms 등) 이름·주석·사용처로 의도를 확인하고, 모르면 "의도 확인 필요"로.
  - **분기 범위 오기**: "성공 시에만 X" 처럼 쓰기 전에 X 호출이 실제로 어느 블록(if/match 팔·? 이후·finally)에 있는지
    줄 번호로 확인한다. 한 단위에서 같은 패턴을 여러 핸들러에 복붙해 요약하면 같은 오류가 반복된다.
  - **주석 과장**: 주석에 없는 내용을 주석이 말한다고 쓰지 않는다 — 주석 인용은 원문 그대로.
  - **집계 수치 인용**: "N개 파일 M곳" 같은 수치는 어떤 도구·조건으로 센 것인지 함께 쓴다 (LSP 참조 수와 grep 수는 다르다).

완료가 거부되면(`gate_failed`) `data.missing` 의 심볼을 처리하고 다시 완료한다.

## 2. 병렬 분석 (선택)

단위가 많으면 서브에이전트를 여러 개 띄우고 각자 다른 `agent` id 로 `oss_unit_next` 를 호출한다. 임대(lease) 덕분에 같은 단위를 두 번 잡지 않는다.

## 3. 독립 검토

`oss_claims_pending` 의 결론마다 **작성자가 아닌** 에이전트가 근거 위치를 직접 읽고 `oss_claim_review(verdict, reviewer)`.
반려되면 미확인이 생기므로 해당 단위를 다시 본다.

## 4. 미확인 처리

`oss_unknowns_list` 를 심각도순으로 보며:
- 도구로 확인 가능하면 LSP 근거를 만들어 `oss_unknown_update(resolution={"disposition":"verified", ...})`
- 범위 밖이면 `out-of-scope` + 사유
- 지금 도구로 못 푸는 것(L2·L3 필요)은 열어 둔다 — 리포트에 그대로 남는 게 정상

## 5. 마무리

1. `oss_report_coverage` — `gates` 중 실패 항목을 사용자에게 그대로 보고 (실패를 숨기지 않는다).
2. `oss_report_export` — Markdown/JSON 경로를 알려준다.
3. 보고 형식: 기능 카탈로그 요약 → 단위별 핵심 동작 → 열린 미확인(심각도순) → 커스텀 시 주의할 확장 포인트.

## 하지 말 것

- `secret` 범주 파일을 읽거나 인용하지 않는다 (`.env`, 키 파일 등).
- 근거 없이 "전부 분석했다"고 말하지 않는다 — `complete=true` 일 때만.
- 분석 대상 코드를 빌드·실행하지 않는다 (격리 실행은 3단계 기능).
