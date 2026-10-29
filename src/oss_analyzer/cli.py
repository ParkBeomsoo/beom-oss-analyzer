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
from oss_analyzer.core import doctor, engines, inventory, ledger, loop, project, report, unknowns


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
            return dataflow.build(conn, settings, proj, args.language)
        if not args.json:
            raise ToolError("invalid_request", "--json 질의 파일이 필요함 (또는 --build-only)")
        return dataflow.query(conn, settings, proj, _as_list(_load_json(args.json)), args.language)
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
        return {"results": out} if isinstance(raw, list) else out[0]
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
    if cmd == "classify":
        return loop.classify_files(conn, paths=args.paths, category=args.category, reason=args.reason)
    raise ToolError("invalid_request", f"알 수 없는 명령: {cmd}")


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
