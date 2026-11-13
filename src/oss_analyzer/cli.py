"""CLI — MCP 와 같은 기능을 터미널에서 실행 (배치·CI·서브에이전트용). 결과는 JSON envelope.

JSON 인자가 필요한 명령은 --json <파일 경로> 또는 --json - (stdin) 으로 받는다.
LSP 질의는 언어 서버 기동 비용이 커서 여러 질의를 한 번에 받는 배치(lsp) 명령으로만 제공한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from oss_analyzer.adapters.lsp import LspPool, ProcessLock
from oss_analyzer.common.response import Envelope, ToolError
from oss_analyzer.config import load_settings
from oss_analyzer.core import customize, doctor, engines, extpoints, inventory, ledger, loop, port, project, report, runtime, unknowns
from oss_analyzer.core import port_guard
from oss_analyzer.core import license as license_mod


def _print(data: object) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _load_json(value: str) -> Any:
    if value == "-":
        sys.stdin.reconfigure(encoding="utf-8")
        return json.loads(sys.stdin.read())
    return json.loads(Path(value).read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oss-analyzer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="엔진 점검")
    p.add_argument("--languages", nargs="*")
    p = sub.add_parser("open", help="저장소 열기 (커밋 고정)")
    p.add_argument("source")
    p.add_argument("--ref")
    p.add_argument("--name")
    sub.add_parser("list", help="프로젝트 목록")
    p = sub.add_parser("setup", help="엔진 설치 계획(기본) / --apply 로 고른 것만 설치")
    p.add_argument("--languages", nargs="*")
    p.add_argument("--apply", nargs="+", metavar="ID", help="plan 의 components[].id (체크섬 검증 후 설치)")
    p.add_argument("--remove", nargs="+", metavar="ID", help="되돌리기: skill, mcp-register (이 툴킷이 설치한 것만)")
    p.add_argument("--update", action="store_true", help="툴킷 업데이트 뒤 — 이미 설치한 스킬만 새 버전으로 (새로 설치하지 않음)")
    p.add_argument("--scope", choices=["user", "project"], default="user",
                   help="스킬·MCP 연동 범위: user(모든 프로젝트) / project(--project-dir 한 곳만)")
    p.add_argument("--project-dir", help="--scope project 의 프로젝트 폴더 (기본: 현재 폴더)")

    for name, help_ in (("coverage", "커버리지"), ("features", "기능 카탈로그"), ("unknowns", "열린 미확인"),
                        ("units", "단위 목록"), ("export", "리포트 내보내기"), ("pending", "독립 검토 대기 결론")):
        sub.add_parser(name, help=help_).add_argument("project_id")
    p = sub.add_parser("inventory", help="인벤토리 생성")
    p.add_argument("project_id")
    p.add_argument("--rebuild", action="store_true")

    p = sub.add_parser("next", help="다음 단위 임대")
    p.add_argument("project_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--kind")
    p = sub.add_parser("claim", help="특정 단위 지정 임대")
    p.add_argument("project_id")
    p.add_argument("unit_id")
    p.add_argument("--agent", required=True)
    p = sub.add_parser("renew", help="단위 임대 연장")
    p.add_argument("project_id")
    p.add_argument("unit_id")
    p.add_argument("--agent", required=True)
    p = sub.add_parser("context", help="단위 코드·심볼·호출 간선")
    p.add_argument("project_id")
    p.add_argument("unit_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--offset", type=int, default=0, help="단위 안의 줄 순번 (파일 줄 번호 아님). 이전 응답의 next_offset")
    p.add_argument("--no-calls", action="store_true", help="호출 간선 생략")
    p.add_argument("--all-calls", action="store_true", help="신뢰도 낮은 이름 기반 간선도 포함")
    p.add_argument("--raw", action="store_true", help="JSON 대신 사람이 읽는 텍스트(코드·심볼·간선)로 출력")
    p = sub.add_parser("dataflow", help="데이터 흐름(L2, Joern) 질의 (--json: [{path, line, sink}])")
    p.add_argument("project_id")
    p.add_argument("--json")
    p.add_argument("--language")
    p.add_argument("--build-only", action="store_true", help="CPG 만 만든다 (처음 한 번 오래 걸림)")
    p.add_argument("--scope", nargs="+", help="이 경로 접두 아래 코드만 CPG 로 (테스트·외부 코드는 항상 제외)")
    p.add_argument("--rebuild", action="store_true")
    p = sub.add_parser("finding", help="결론 기록 (--json: {text, severity, evidence, symbol_ids} 또는 그 배열)")
    p.add_argument("project_id")
    p.add_argument("unit_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--json", required=True)
    p = sub.add_parser("withdraw", help="내 결론 철회 (완료된 단위도 가능, 단위 등급 재계산)")
    p.add_argument("project_id")
    p.add_argument("claim_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("mark", help="특이사항 없는 심볼 검토 완료 처리")
    p.add_argument("project_id")
    p.add_argument("unit_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--symbols", nargs="+", required=True)
    p = sub.add_parser("complete", help="단위 완료")
    p.add_argument("project_id")
    p.add_argument("unit_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--summary", required=True)
    p = sub.add_parser("lsp", help="LSP 질의 배치 (--json: [{op: references|definition|incoming|outgoing, path, line, column}])")
    p.add_argument("project_id")
    p.add_argument("--json", required=True)
    p = sub.add_parser("unknown-add", help="미확인 기록 (--json: {question, domain, severity, required_level, scope?, ...} 또는 그 배열)")
    p.add_argument("project_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--json", required=True)
    p = sub.add_parser("unknown-update", help="미확인 갱신 (--json: {unknown_id, expected_revision, status?, resolution?, ...})")
    p.add_argument("project_id")
    p.add_argument("--agent", required=True)
    p.add_argument("--json", required=True)
    p = sub.add_parser("review", help="결론 독립 검토")
    p.add_argument("project_id")
    p.add_argument("claim_id")
    p.add_argument("--reviewer", required=True)
    p.add_argument("--verdict", required=True, choices=["confirmed", "rejected"])
    p.add_argument("--note", required=True)
    p = sub.add_parser("impact", help="영향도: 대상(심볼 id·경로:줄·파일·이름)을 바꾸면 영향받는 곳")
    p.add_argument("project_id")
    p.add_argument("targets", nargs="+")
    p.add_argument("--depth", type=int, default=4)
    p.add_argument("--verify", action="store_true", help="LSP incoming 으로 대조 (L1 근거 기록, 누락 호출자 보탬)")
    p.add_argument("--no-name-match", action="store_true", help="이름 일치 후보 생략")
    p = sub.add_parser("extpoints", help="확장 포인트 목록")
    p.add_argument("project_id")
    p.add_argument("--kind")
    p.add_argument("--path")
    p = sub.add_parser("custom-add", help="커스텀 기록 (대상 심볼 앵커 저장 + 영향도 요약)")
    p.add_argument("project_id")
    p.add_argument("--description", required=True)
    p.add_argument("--kind", default="modify", choices=sorted(customize.KINDS))
    p.add_argument("--extension-point")
    p.add_argument("--agent", default="cli")
    p.add_argument("--targets", nargs="+", default=[], help="심볼 id·경로:줄·파일·이름 (확장 포인트만 쓸 때는 생략)")
    p = sub.add_parser("custom-list", help="커스텀 원장")
    p.add_argument("project_id")
    p.add_argument("--status")
    p = sub.add_parser("custom-update", help="커스텀 상태 변경")
    p.add_argument("project_id")
    p.add_argument("customization_id")
    p.add_argument("--status", required=True, choices=sorted(customize.STATUSES))
    p.add_argument("--note", required=True)
    p.add_argument("--agent", default="cli")
    p = sub.add_parser("compare", help="버전 비교 (base=우리 기준 버전, head=업스트림 새 버전)")
    p.add_argument("base_project_id")
    p.add_argument("head_project_id")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--save", action="store_true", help="head 작업 공간 reports/ 에 JSON 저장")
    p = sub.add_parser("runtime-ingest", help="커버리지 리포트 → 심볼 단위 L3 근거")
    p.add_argument("project_id")
    p.add_argument("report")
    p.add_argument("--root", nargs="*", default=[], help="리포트 절대 경로에서 잘라낼 실행 디렉터리")
    p.add_argument("--tool")
    p = sub.add_parser("runtime-run", help="저장소 사본에서 명령 실행 후 커버리지 적재 (Docker, 없으면 --trusted 만)")
    p.add_argument("project_id")
    p.add_argument("--command", required=True)
    p.add_argument("--report", required=True, help="명령이 만드는 리포트 경로 (사본 루트 기준)")
    p.add_argument("--trusted", action="store_true", help="Docker 없이 로컬 실행 허용 (신뢰하는 저장소만)")
    p.add_argument("--image")
    p.add_argument("--network", action="store_true", help="컨테이너 네트워크 허용 (기본 차단)")
    p.add_argument("--timeout", type=int, default=1800)
    sub.add_parser("runtime-runs", help="적재한 커버리지 실행 목록").add_argument("project_id")
    sub.add_parser("license", help="라이선스 스캔").add_argument("project_id")
    p = sub.add_parser("slice", help="포팅 범위: 진입점('POST /api/x')·심볼에서 앞으로 닫힘 + 상태·등록·외부 의존·테스트·라이선스")
    p.add_argument("project_id")
    p.add_argument("targets", nargs="+")
    p.add_argument("--within", nargs="+", help="포팅 경계(경로 접두). 밖으로 나가는 호출은 boundary(대체할 의존)로")
    p.add_argument("--depth", type=int, default=10)
    p = sub.add_parser("port-create", help="포팅 원장 생성 (슬라이스 항목마다 pending)")
    p.add_argument("project_id")
    p.add_argument("--name", required=True)
    p.add_argument("--targets", nargs="+", required=True)
    p.add_argument("--language", required=True, help="대상 언어")
    p.add_argument("--target-root", required=True, help="대상(내) 저장소 경로")
    p.add_argument("--within", nargs="+", help="포팅 경계(경로 접두)")
    p.add_argument("--depth", type=int, default=10)
    p.add_argument("--agent", default="cli")
    p = sub.add_parser("port-map", help="항목 매핑 (--json: [{item, status: ported|omitted|replaced, target?: '경로:줄', note?}])")
    p.add_argument("project_id")
    p.add_argument("port_id")
    p.add_argument("--json", help="매핑 파일")
    p.add_argument("--follow", action="store_true", help="이동한 ported 대상을 새 줄로 갱신")
    p.add_argument("--agent", default="cli")
    p = sub.add_parser("port-accept", help="의도적인 동작 차이 허용 제안 (케이스 이름 패턴, * 가능) — 사용자 승인 전까지 게이트 미충족")
    p.add_argument("project_id")
    p.add_argument("port_id")
    p.add_argument("--case", nargs="+", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--agent", default="cli")
    p = sub.add_parser("data", help="저장 위치·크기, 휴지통·정리 (홈 폴더 하나에 성격별로 나눠 둔다)")
    p.add_argument("--trash", metavar="RUN_ID", help="실행을 휴지통으로 (원장은 남음)")
    p.add_argument("--restore", metavar="NAME", help="휴지통에서 되살리기 (휴지통 목록의 name)")
    p.add_argument("--empty-trash", action="store_true", help="휴지통 비우기")
    p.add_argument("--prune", type=int, metavar="DAYS", help="끝났거나 숨긴 실행 중 DAYS 일 동안 그대로인 것을 휴지통으로")
    p.add_argument("--dry-run", action="store_true", help="--prune 대상만 보기")
    p.add_argument("--set-cleanup", type=int, metavar="DAYS", help="앱·CLI 시작 때 자동 정리 (0 = 끔)")
    p.add_argument("--no-sizes", action="store_true", help="크기 계산 생략 (빠름)")
    p = sub.add_parser("app", help="데스크톱 앱 열기 (pip install 'beom-oss-analyzer[app]')")
    p.add_argument("--shortcut", nargs="?", const="start,desktop", metavar="start,desktop",
                   help="앱을 열지 않고 바로가기만 만든다 — 시작 메뉴(검색에 나옴)·바탕화면")
    p.add_argument("--remove-shortcut", action="store_true", help="만든 바로가기를 지운다")
    p = sub.add_parser("run", help="자동 실행 — Claude Code 로 단계를 끝까지 진행 (승인·질문은 앱에서)")
    p.add_argument("--mode", choices=["request", "oss"], help="새 실행일 때 필수")
    p.add_argument("--target-root", help="새 실행일 때 필수")
    p.add_argument("--name")
    p.add_argument("--request", default="", help="요청사항 (request 모드) 또는 추가 요청 (oss 모드)")
    p.add_argument("--source", help="원본 저장소 (oss 모드)")
    p.add_argument("--feature", help="옮길 기능 (oss 모드)")
    p.add_argument("--max-rounds", type=int, default=8)
    p.add_argument("--model")
    p.add_argument("--auth", choices=["subscription", "api_key"], default="subscription")
    p.add_argument("--agent", choices=["claude", "codex", "gemini"], default="claude")
    p.add_argument("--create-only", action="store_true", help="실행 준비만 (앱에서 시작)")
    p.add_argument("--resume", metavar="RUN_ID", help="멈춘 실행 이어서")
    sub.add_parser("runs", help="실행 목록")
    sub.add_parser("agent-status", help="Claude Code 설치·로그인 확인")
    p = sub.add_parser("make-create", help="요청사항 기반 만들기 시작 (원본 없이 요구사항이 항목이 되는 원장)")
    p.add_argument("--name", required=True)
    p.add_argument("--request", required=True, help="사용자 요청 원문")
    p.add_argument("--target-root", required=True, help="만들 곳 (내 저장소 또는 새 폴더)")
    p.add_argument("--context", help="함께 볼 분석 프로젝트 id (넣을 저장소·참고 저장소를 open·inventory 한 것)")
    p.add_argument("--agent", default="cli")
    p = sub.add_parser("make-req", help="요구사항 목록 정하기 (--json: [{id?: R1, text}]) — 사용자 승인 대상")
    p.add_argument("project_id")
    p.add_argument("port_id")
    p.add_argument("--json", help="요구사항 파일 (없으면 지금 목록 보기)")
    p.add_argument("--agent", default="cli")
    p = sub.add_parser("port-status", help="포팅 진행·게이트·다음 할 일")
    p.add_argument("project_id")
    p.add_argument("port_id", nargs="?")
    p = sub.add_parser("port-approve", help="사람 확인 항목(차이 허용·생략·케이스 변경) 승인 — 사용자가 터미널에서 직접 실행")
    p.add_argument("project_id")
    p.add_argument("port_id")
    p.add_argument("--list", action="store_true", help="승인 대기 목록만 보기 (누구나)")
    p = sub.add_parser("port-finish", help="완료 판정 — 게이트가 모두 충족될 때만 발급")
    p.add_argument("project_id")
    p.add_argument("port_id")
    p.add_argument("--agent", default="cli")
    for name, help_ in (("port-capture", "원본에 케이스를 돌려 골든 저장"), ("port-verify", "대상(내 코드)을 원본과 차등 비교")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("project_id")
        p.add_argument("port_id")
        p.add_argument("--cases", help="케이스 JSON ({defaults?, cases: [{name?, method, path, json?, ignore?, match?, compare?} "
                                         "| {argv, stdin?} | {call: {source, target}, args?, kwargs?, runtime?}]})")
        p.add_argument("--source-url")
        p.add_argument("--source-cmd", help="원본 서버 기동 명령 (분석 clone 의 임시 사본에서, --trusted 필요)")
        p.add_argument("--source-cli", help="원본 CLI 명령 (케이스 argv 를 뒤에 붙임)")
        p.add_argument("--trusted", action="store_true")
        p.add_argument("--source-image", help="원본을 이 Docker 이미지 안에서 실행 (격리, --trusted 불필요)")
        p.add_argument("--explore", type=int, default=0, help="입력 변형 케이스를 최대 N개 추가 생성")
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--timeout", type=float, default=30)
        if name == "port-capture":
            p.add_argument("--coverage-cmd", help="원본 실행 후 커버리지 리포트를 만드는 명령 (원본 사본에서)")
            p.add_argument("--coverage-report", help="원본 커버리지 리포트 경로 (사본 기준) → 케이스 충분성 게이트")
        if name == "port-verify":
            p.add_argument("--target-url")
            p.add_argument("--target-cmd", help="대상 서버 기동 명령 (대상 저장소에서)")
            p.add_argument("--target-cli")
            p.add_argument("--concurrency", type=int, default=0, help="케이스마다 대상에 동시 요청 N개")
            p.add_argument("--target-coverage-cmd", help="대상 실행 후 커버리지 리포트를 만드는 명령 (대상 저장소에서)")
            p.add_argument("--target-coverage-report", help="대상 커버리지 리포트 경로 (대상 저장소 기준) → ported 대상 실행 여부")
    p = sub.add_parser("classify", help="파일 직접 분류")
    p.add_argument("project_id")
    p.add_argument("--category", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("paths", nargs="+")
    return parser


def _run(args: argparse.Namespace) -> Any:
    settings = load_settings()
    if args.cmd == "doctor":
        return doctor.run(settings, args.languages)
    if args.cmd == "open":
        return project.open_repo(settings, args.source, args.ref, args.name)
    if args.cmd == "list":
        return {"projects": project.list_projects(settings)}
    if args.cmd == "setup":
        from oss_analyzer.core import setup

        pdir = Path(args.project_dir) if args.project_dir else None
        if args.update:
            return setup.update(settings, args.scope, pdir)
        if args.apply:
            return setup.apply(settings, args.apply, args.languages, args.scope, pdir)
        if args.remove:
            return setup.remove(settings, args.remove, args.scope, pdir)
        return setup.plan(settings, args.languages, args.scope, pdir)
    if args.cmd == "data":
        from oss_analyzer.core import storage

        storage.migrate(settings)
        if args.trash:
            return storage.trash_run(settings, args.trash)
        if args.restore:
            return storage.restore_run(settings, args.restore)
        if args.empty_trash:
            return storage.empty_trash(settings)
        if args.prune is not None:
            return storage.prune_runs(settings, args.prune, dry_run=args.dry_run)
        if args.set_cleanup is not None:
            return storage.save_prefs(settings, {"cleanup_days": args.set_cleanup or None})
        return storage.report(settings, sizes=not args.no_sizes)
    if args.cmd == "app":
        from oss_analyzer.core import shortcut

        if args.remove_shortcut:
            return shortcut.remove()
        try:
            from oss_analyzer.app import main as app_main
        except ImportError as e:
            raise ToolError("not_found", f"데스크톱 앱 구성요소가 없음 ({e.name})",
                            recovery="pip install 'beom-oss-analyzer[app]'") from None
        if args.shortcut:
            where = tuple(w.strip() for w in args.shortcut.split(",") if w.strip() in shortcut.LOCATIONS) or shortcut.LOCATIONS
            return shortcut.create(settings, where, icon=app_main.write_icon_files(settings.home))
        return {"exit_code": app_main.main([])}
    if args.cmd == "agent-status":
        from oss_analyzer.core import runner

        return runner.agent_status()
    if args.cmd == "runs":
        from oss_analyzer.core import inbox

        return {"runs": [{k: r.get(k) for k in ("id", "name", "mode", "status", "round", "max_rounds", "project_id", "port_id")}
                         for r in inbox.list_runs(settings)]}
    if args.cmd == "run":
        from oss_analyzer.core import runner

        if args.resume:
            run_id = args.resume
        elif not (args.mode and args.target_root):
            raise ToolError("invalid_request", "새 실행에는 --mode 와 --target-root 가 필요함 (이어서 할 때는 --resume RUN_ID)")
        else:
            run_id = runner.create(settings, mode=args.mode, target_root=args.target_root, request=args.request,
                                   name=args.name, source=args.source, feature=args.feature, max_rounds=args.max_rounds,
                                   model=args.model, auth=args.auth, agent=args.agent)["id"]
            if args.create_only:
                return {"run_id": run_id}
        sys.stderr.write(f"실행 {run_id} — 승인·질문은 데스크톱 앱(beom-oss-analyzer)에서 답한다\n")
        return runner.Runner(settings, run_id).run()
    if args.cmd == "make-create":
        from oss_analyzer.core import make

        return make.create(settings, name=args.name, request=args.request, target_root=args.target_root,
                           context=args.context, author=args.agent)
    if args.cmd == "compare":
        out = customize.compare_versions(settings, args.base_project_id, args.head_project_id, args.limit)
        if args.save:
            out["saved_to"] = str(customize.workspace_report(settings, args.head_project_id, args.base_project_id, out))
        return out

    conn, proj = project.load(settings, args.project_id)
    cmd = args.cmd
    if cmd == "inventory":
        return inventory.build(conn, settings, proj, args.rebuild)
    if cmd == "coverage":
        return report.coverage(conn)
    if cmd == "features":
        return report.features(conn, 0, 10000)
    if cmd == "unknowns":
        return unknowns.list_(conn, limit=1000)
    if cmd == "units":
        return loop.list_units(conn, None, 0, 10000)
    if cmd == "export":
        return report.export(conn, settings, proj)
    if cmd == "pending":
        return {"items": ledger.rows_to_dicts(conn.execute(
            "SELECT id, unit_id, text, severity, evidence_ids, level, author FROM claim WHERE review_status = 'pending' "
            "ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, created_at").fetchall())}
    if cmd == "next":
        return loop.next_unit(conn, settings, args.agent, args.kind)
    if cmd == "claim":
        return loop.claim_unit(conn, settings, args.agent, args.unit_id)
    if cmd == "renew":
        return loop.renew(conn, args.unit_id, args.agent)
    if cmd == "context":
        out = loop.unit_context(conn, settings, proj, args.unit_id, args.agent, args.offset,
                                include_calls=not args.no_calls, all_calls=args.all_calls)
        if args.raw:
            return _RawText(_render_context(out))
        return out
    if cmd == "dataflow":
        from oss_analyzer.core import dataflow

        if args.build_only:
            return dataflow.build(conn, settings, proj, args.language, args.rebuild, args.scope)
        if not args.json:
            raise ToolError("invalid_request", "--json 질의 파일이 필요함 (또는 --build-only)")
        return dataflow.query(conn, settings, proj, _as_list(_load_json(args.json)), args.language, args.scope)
    if cmd == "finding":
        raw = _load_json(args.json)
        out = []
        for body in _as_list(raw):
            try:
                out.append({"ok": True, **loop.record_finding(
                    conn, proj, unit_id=args.unit_id, agent=args.agent, text=body["text"],
                    severity=body.get("severity", "info"), evidence_items=body.get("evidence", []),
                    symbol_ids=body.get("symbol_ids", []), replaces=body.get("replaces"))})
            except ToolError as e:
                out.append({"ok": False, "text": str(body.get("text", ""))[:80], "error": {"code": e.code, "message": e.message}})
        # 배열로 넣으면 항상 {"results": [...]}, 객체 하나면 그 결과 그대로
        if isinstance(raw, list):  # 일부 실패를 ok:true 에 묻히지 않게 집계
            return {"recorded": sum(1 for r in out if r["ok"]), "failed": sum(1 for r in out if not r["ok"]), "results": out}
        return out[0]
    if cmd == "withdraw":
        return loop.withdraw_claim(conn, claim_id=args.claim_id, agent=args.agent, reason=args.reason)
    if cmd == "mark":
        return loop.mark_symbols(conn, unit_id=args.unit_id, agent=args.agent, symbol_ids=args.symbols, note=args.note)
    if cmd == "complete":
        return loop.complete_unit(conn, unit_id=args.unit_id, agent=args.agent, summary=args.summary)
    if cmd == "lsp":
        return _lsp_batch(conn, settings, proj, _load_json(args.json))
    if cmd == "unknown-add":
        raw = _load_json(args.json)
        out = []
        with ledger.tx(conn):
            for body in _as_list(raw):
                try:
                    out.append(unknowns.record(conn, origin=args.agent, **body))
                except (ToolError, TypeError) as e:
                    out.append({"created": False, "error": str(getattr(e, "message", e))})
        return {"results": out} if isinstance(raw, list) else out[0]
    if cmd == "unknown-update":
        body = _load_json(args.json)
        uid = body.pop("unknown_id")
        with ledger.tx(conn):
            return unknowns.update(conn, uid, actor=args.agent, **body)
    if cmd == "review":
        return loop.review_claim(conn, claim_id=args.claim_id, reviewer=args.reviewer, verdict=args.verdict, note=args.note)
    if cmd == "impact":
        if not args.verify:
            return customize.impact(conn, proj, args.targets, args.depth, name_match=not args.no_name_match)
        pool = LspPool(settings.lsp_idle_seconds, settings.lsp_timeout_seconds)
        try:
            return customize.impact(conn, proj, args.targets, args.depth, True, pool, not args.no_name_match)
        finally:
            pool.close_repo(Path(proj["repo_dir"]))
    if cmd == "extpoints":
        return extpoints.list_(conn, args.kind, args.path, 0, 10000)
    if cmd == "custom-add":
        return customize.record(conn, proj, description=args.description, targets=args.targets, kind=args.kind,
                                extension_point_id=args.extension_point, author=args.agent)
    if cmd == "custom-list":
        return customize.list_customizations(conn, args.status)
    if cmd == "custom-update":
        return customize.update_customization(conn, args.customization_id, status=args.status, note=args.note, actor=args.agent)
    if cmd == "runtime-ingest":
        return runtime.ingest(conn, proj, Path(args.report), tool=args.tool, roots=args.root)
    if cmd == "runtime-run":
        return runtime.run(conn, proj, settings, args.command, report=args.report, trusted=args.trusted, image=args.image,
                           timeout=args.timeout, network=args.network)
    if cmd == "runtime-runs":
        return runtime.runs(conn)
    if cmd == "license":
        return license_mod.scan(conn, proj)
    if cmd == "slice":
        return port.slice_(conn, proj, args.targets, depth=args.depth, within=args.within)
    if cmd == "port-create":
        return port.create(conn, proj, name=args.name, targets=args.targets, target_language=args.language,
                           target_root=args.target_root, author=args.agent, within=args.within, depth=args.depth)
    if cmd == "port-map":
        if args.follow:
            return port.follow(conn, args.port_id, args.agent)
        if not args.json:
            raise ToolError("invalid_request", "--json 또는 --follow 가 필요함")
        return port.map_items(conn, args.port_id, _as_list(_load_json(args.json)), args.agent)
    if cmd == "port-accept":
        return port.accept(conn, args.port_id, args.case, args.reason, args.agent)
    if cmd == "make-req":
        from oss_analyzer.core import make

        if not args.json:
            return {"requirements": make.requirements(conn, args.port_id)}
        return make.set_requirements(conn, args.port_id, _as_list(_load_json(args.json)), args.agent)
    if cmd == "port-status":
        return port.status_(conn, args.port_id) if args.port_id else port.list_(conn)
    if cmd == "port-approve":
        return _port_approve(conn, args.port_id, list_only=args.list)
    if cmd == "port-finish":
        return port.finish(conn, args.port_id, args.agent)
    if cmd == "port-capture":
        return port.capture(conn, proj, settings, args.port_id, args.cases, source_url=args.source_url,
                            source_cmd=args.source_cmd, source_cli=args.source_cli, trusted=args.trusted, timeout=args.timeout,
                            source_image=args.source_image, explore_n=args.explore, seed=args.seed,
                            coverage_cmd=args.coverage_cmd, coverage_report=args.coverage_report)
    if cmd == "port-verify":
        return port.verify(conn, proj, settings, args.port_id, target_url=args.target_url, target_cmd=args.target_cmd,
                           target_cli=args.target_cli, cases_file=args.cases, source_url=args.source_url,
                           source_cmd=args.source_cmd, source_cli=args.source_cli, trusted=args.trusted, timeout=args.timeout,
                           source_image=args.source_image, explore_n=args.explore, seed=args.seed, concurrency=args.concurrency,
                           target_coverage_cmd=args.target_coverage_cmd, target_coverage_report=args.target_coverage_report)
    if cmd == "classify":
        return loop.classify_files(conn, paths=args.paths, category=args.category, reason=args.reason)
    raise ToolError("invalid_request", f"알 수 없는 명령: {cmd}")


def _port_approve(conn, port_id: str, *, list_only: bool) -> dict:
    """사람이 터미널에서 직접 승인. 표준 입력·출력이 실제 터미널이 아니면(에이전트가 파이프로 실행) 거부한다."""
    items = port.pending_reviews(conn, port_id)
    if list_only or not items:
        return {"pending": items, "note": "승인 대기 없음" if not items else "사용자가 터미널에서 port-approve 를 직접 실행해 승인"}
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ToolError("human_required", "승인은 사용자가 터미널에서 직접 입력해야 함 (지금 입력이 터미널이 아님)",
                        recovery="사용자에게 이 명령을 터미널에서 직접 실행해 달라고 요청하거나, MCP oss_port_approve 확인 창 사용",
                        data={"pending": [{k: i[k] for k in ("item_id", "kind", "summary")} for i in items]})
    sys.stderr.write(f"승인 대기 {len(items)}건\n" + port_guard.render(items) + "\n\n")
    sys.stderr.write("모두 승인하려면 '승인', 일부만이면 번호(예: 1,3), 취소는 Enter: ")
    sys.stderr.flush()
    answer = sys.stdin.readline().strip()
    if answer == "승인":
        chosen = items
    elif answer:
        try:
            idx = {int(x) for x in answer.replace(" ", "").split(",") if x}
        except ValueError:
            raise ToolError("invalid_request", "번호는 쉼표로 구분 (예: 1,3)") from None
        chosen = [it for i, it in enumerate(items, 1) if i in idx]
    else:
        return {"approved": [], "note": "취소함"}
    done = port_guard.approve(conn, port_id, chosen, channel="cli-tty")
    return {"approved": done, **{k: v for k, v in port.status_(conn, port_id, brief=True).items()
                                 if k in ("gates", "next_step", "approvals")}}


def _lsp_batch(conn, settings, proj: dict, queries: list[dict]) -> dict:
    """질의마다 실패를 격리한다 (하나가 터져도 나머지 결과는 돌려준다).

    같은 (프로젝트, 언어) 언어 서버는 프로세스 잠금으로 한 번에 하나만 띄운다.
    """
    if not isinstance(queries, list):
        raise ToolError("invalid_request", "lsp --json 은 질의 배열이어야 함: [{op, path, line, column}, ...]")
    pool = LspPool(settings.lsp_idle_seconds, settings.lsp_timeout_seconds)
    results: list[dict | None] = [None] * len(queries)
    by_lang: dict[str, list[int]] = {}
    for i, q in enumerate(queries):
        row = conn.execute("SELECT language FROM file WHERE path = ?", (q.get("path"),)).fetchone()
        by_lang.setdefault((row["language"] if row else None) or "?", []).append(i)
    try:
        for lang, idxs in by_lang.items():
            lock = ProcessLock(settings.home / "locks" / f"lsp-{proj['id']}-{lang}")
            with lock:
                for i in idxs:
                    q = queries[i]
                    try:
                        op, path, line, col = q["op"], q["path"], int(q["line"]), int(q["column"])
                        if op == "references":
                            out = engines.lsp_references(conn, pool, proj, path, line, col)
                        elif op == "definition":
                            out = engines.lsp_definition(conn, pool, proj, path, line, col)
                        elif op in ("incoming", "outgoing"):
                            out = engines.lsp_call_hierarchy(conn, pool, proj, path, line, col, op)
                        else:
                            raise ToolError("invalid_request", f"op 는 references/definition/incoming/outgoing: {op}")
                        if not out.get("count"):
                            out["note"] = ("결과 0건 — 실제로 없음/외부 라이브러리만 해당/언어 서버 인덱싱 미완료 중 무엇인지 "
                                           "구분할 수 없음. 0건을 '호출 없음'의 근거로 쓰지 말 것")
                        results[i] = {"query": q, "ok": True, **out}
                    except ToolError as e:
                        results[i] = {"query": q, "ok": False, "error": {"code": e.code, "message": e.message}}
                    except Exception as e:  # noqa: BLE001 — 언어 서버 내부 오류도 질의 단위로 격리
                        results[i] = {"query": q, "ok": False, "error": {"code": "engine_error", "message": f"{type(e).__name__}: {e}"}}
            pool.close_repo(Path(proj["repo_dir"]))
    finally:
        pool.close_repo(Path(proj["repo_dir"]))
    return {"results": results}


class _RawText(str):
    """--raw 출력 표시용."""


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def _render_context(ctx: dict) -> str:
    out = [f"# unit {ctx['unit_id']} @ {ctx['commit'][:12]}  (줄 {ctx['total_lines']}, next_offset={ctx['next_offset']}"
           f"{' → ' + ctx['next_line'] if ctx.get('next_line') else ''})"]
    if ctx.get("masked_lines"):
        out.append(f"# 비밀값 가림: {', '.join(ctx['masked_lines'][:20])}")
    out.append(ctx["code"])
    if "symbols" in ctx:
        out.append("# symbols (id | label | name | path:start-end | reviewed)")
        out += [f"{s['id']} | {s['label']} | {s['name']} | {s['path']}:{s['start_line']}-{s['end_line']} | {s['reviewed']}"
                for s in ctx["symbols"]]
    if ctx.get("calls"):
        out.append(f"# calls (L0, 이름 기반 — 숨긴 약한 간선 {ctx.get('calls_hidden_weak', 0)}개)")
        out += [f"{c['caller']} -> {c['callee']}  @{c['caller_path']}:{c['call_line']}  conf={c['confidence']} {c['strategy']}  {c['evidence_id']}"
                for c in ctx["calls"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # --help 포함 모든 출력 UTF-8
    sys.stderr.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    env = Envelope(f"cli.{args.cmd}")
    try:
        result = _run(args)
        if isinstance(result, _RawText):
            sys.stdout.write(result)
        else:
            _print(env.ok(result))
        return 0
    except ToolError as e:
        _print(env.fail(e.code, e.message, e.recovery, e.data))
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _print(env.fail("invalid_request", f"--json 입력을 UTF-8 JSON 으로 읽지 못함: {e}",
                        "UTF-8 로 저장했는지, 백슬래시가 \\\\ 로 이스케이프됐는지 확인"))
        return 1
    except FileNotFoundError as e:
        _print(env.fail("invalid_request", f"파일 없음: {e.filename}"))
        return 1
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 JSON envelope 으로 (스크립트가 파싱할 수 있게)
        import traceback

        retry = "locked" in str(e) or "busy" in str(e)
        _print(env.fail("internal", f"{type(e).__name__}: {e}",
                        "잠시 후 같은 명령을 다시 실행" if retry else "오류 내용을 보고",
                        {"traceback_tail": traceback.format_exc().splitlines()[-6:]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
