"""라우트 후처리 — 규칙팩이 찾은 등록 지점을 '실제 URL 목록'으로 만든다.

1. 동적 경로 해석: format!/템플릿/연결 + 호출부 인자 표 → 경로마다 기능 행 추가 (resolve.py)
2. 마운트 조합: Axum nest("/p", sub()) → sub() 안 라우트에 접두
3. Spring 클래스 접두: 클래스 @RequestMapping + 메서드 @GetMapping
4. HTTP 메서드 추출, 경로 정규화 (// 정리)
해석이 설정 기본값에 기댔거나 일부를 못 펼친 경우 그 사실을 미확인으로 남긴다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from oss_analyzer.core import evidence, ledger, mounts, routetable, unknowns
from oss_analyzer.core.levels import L0, L3
from oss_analyzer.core.resolve import Resolver, Scope, Unresolved, balanced, literal, normalize_path, split_top

AXUM_METHOD_RE = re.compile(r"\b(get|post|put|delete|patch|head|options|any|trace)\s*\(")
SPRING_RE = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b")


def _functions(conn) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute("SELECT name, path, start_line, end_line FROM symbol WHERE label IN ('Function','Method')"):
        out[r["path"]].append(dict(r))
    return out


def _callers_factory(conn):
    def callers(path: str, name: str) -> list[tuple[str, int, dict]]:
        rows = conn.execute(
            "SELECT DISTINCT caller_path, COALESCE(call_line, caller_line) line FROM call_edge "
            "WHERE callee_path = ? AND (callee LIKE ? OR callee = ?)", (path, f"%.{name}", name)).fetchall()
        if not rows:  # 구조 지도에 간선이 없으면 같은 파일에서 직접 찾는다 (이름 기반)
            rows = [{"caller_path": path, "line": 0}]
        return [(r["caller_path"], r["line"], {"path": r["caller_path"], "start_line": max(r["line"], 1), "end_line": max(r["line"], 1)})
                for r in rows]
    return callers


def _methods_from_handler(text: str | None) -> list[str]:
    if not text:
        return []
    return sorted({m.group(1).upper() for m in AXUM_METHOD_RE.finditer(text)})


def _record_feature(conn, project: dict, *, kind: str, name: str, framework: str, path: str, line: int,
                    handler: str | None, method: str | None, full_path: str, assumptions: list[str],
                    derived_from: str | None, locs: list[dict], source: str, in_test: int = 0) -> str | None:
    seen, valid = set(), []
    for loc in [{"path": path, "start_line": line, "end_line": line}] + locs:
        key = (loc["path"], loc["start_line"], loc["end_line"])
        if key in seen:
            continue
        seen.add(key)
        try:
            valid.append(evidence.validate_location(conn, project, loc))
        except Exception:  # noqa: BLE001 — 근거 위치 하나가 틀려도 나머지로 기록
            continue
    if not valid:
        return None
    ev = evidence.record(conn, project, provider=source, operation="route", parameters={"derived_from": derived_from},
                         result={"full_path": full_path, "method": method, "handler": handler},
                         locations=valid, confidence="inferred" if assumptions else "derived",
                         limitations=assumptions, validated=True)
    fid = "f_" + ev["evidence_id"][3:]
    conn.execute(
        "INSERT OR IGNORE INTO feature (id, kind, name, framework, path, start_line, end_line, handler, source, evidence_id, "
        "in_test, method, full_path, assumptions, derived_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, kind, name, framework, path, line, line, handler, source, ev["evidence_id"], in_test, method, full_path,
         ledger.dumps(assumptions), derived_from),
    )
    return fid


def post_process(conn, project: dict, files: list[dict]) -> dict:
    repo = Path(project["repo_dir"])
    functions = _functions(conn)
    code_files = [f["path"] for f in files if f["category"] in ("code", "asset")]
    stats = {"dynamic_resolved": 0, "dynamic_failed": 0, "resolved_routes": 0, "mounted": 0, "spring_prefixed": 0}

    with ledger.tx(conn):
        _route_tables(conn, project, repo, stats)
        _spring(conn, project, repo, stats)
        _dynamic(conn, project, repo, functions, code_files, stats)
        _mounts(conn, functions, stats)
        _router_mounts(conn, project, repo, functions, code_files, stats)
        # 남은 리터럴 경로는 정규화만
        for f in conn.execute("SELECT id, name, handler, method FROM feature WHERE full_path IS NULL AND kind IN ('http','http-mount')").fetchall():
            lit = literal(f["name"].lstrip("&").strip())
            full = normalize_path(lit) if lit is not None else None
            method = f["method"] or ",".join(_methods_from_handler(f["handler"])) or None
            conn.execute("UPDATE feature SET full_path = ?, method = ? WHERE id = ?", (full, method, f["id"]))
        # 숨은 빈틈 방지: 경로·메서드를 못 정한 HTTP 진입점은 조용히 빠지지 않고 미확인으로
        for f in conn.execute("SELECT * FROM feature WHERE kind = 'http' AND in_test = 0 AND (full_path IS NULL OR method IS NULL)").fetchall():
            missing = [x for x, v in (("경로", f["full_path"]), ("HTTP 메서드", f["method"])) if v is None]
            unknowns.record(conn, question=f"{f['path']}:{f['start_line']} 진입점의 {'·'.join(missing)}를 정하지 못함 ({f['name'][:60]})",
                            domain="entrypoint", scope=f"route-incomplete:{f['path']}:{f['start_line']}", severity="medium",
                            required_level=L0, origin="resolver")
            stats["incomplete"] = stats.get("incomplete", 0) + 1
    return stats


def _router_mounts(conn, project: dict, repo: Path, functions, code_files: list[str], stats: dict) -> None:
    """라우터 변수(receiver) 기반 접두 조합 — FastAPI include_router, Express app.use, Gin Group (mounts.py)."""
    rows = conn.execute("SELECT * FROM feature WHERE kind = 'http' AND in_test = 0 AND receiver IS NOT NULL").fetchall()
    if not rows:
        return
    langs = {".py", ".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx", ".go"}
    sources = {p: "\n".join(evidence.read_lines(project, p)) for p in code_files if Path(p).suffix in langs}
    resolver = Resolver(repo, "", functions, _callers_factory(conn), code_files)
    mr = mounts.build(resolver, sources)
    for f in rows:
        path_lit = literal(f["name"].lstrip("&").strip())
        if path_lit is None:
            continue  # 동적 경로는 _dynamic 이 처리하거나 미확인으로 남음
        prefixes, notes = mr.prefixes(f["path"], f["receiver"])
        fulls = sorted({normalize_path("/" + p + "/" + path_lit) if p else normalize_path(path_lit if path_lit.startswith("/") else "/" + path_lit)
                        for p in prefixes})
        assumptions = notes + ([f"여러 마운트 경로: {', '.join(fulls)}"] if len(fulls) > 1 else [])
        conn.execute("UPDATE feature SET full_path = ?, assumptions = ? WHERE id = ?",
                     (fulls[0], ledger.dumps(assumptions), f["id"]))
        stats["router_mounted"] = stats.get("router_mounted", 0) + 1


def _route_tables(conn, project: dict, repo: Path, stats: dict) -> None:
    """선언형 라우트 표 매크로 → 실제 경로 (routetable.py)."""
    rows = conn.execute("SELECT * FROM feature WHERE kind = 'route-table' AND in_test = 0").fetchall()
    if not rows:
        return
    lines_cache: dict[str, list[str]] = {}

    def lines(path: str) -> list[str]:
        if path not in lines_cache:
            lines_cache[path] = evidence.read_lines(project, path)
        return lines_cache[path]

    rust_sources = {r["path"]: lines(r["path"]) for r in conn.execute(
        "SELECT path FROM file WHERE category = 'code' AND language = 'rust'").fetchall()}
    tables, table_feature = [], {}
    for f in rows:
        src = lines(f["path"])
        text = "\n".join(src[f["start_line"] - 1:f["end_line"]])
        macro_values = None
        macro_name = routetable.MACRO_DEF_RE.search(text)
        if macro_name:
            macro_values = routetable.macro_invocation_values(rust_sources, macro_name.group(1))
        following = "" if macro_name else "\n".join(src[f["end_line"]:f["end_line"] + 6])
        try:
            table = routetable.parse_table(f["path"], text, f["start_line"], macro_values, following)
        except Unresolved:
            table = None
        if table is None:
            unknowns.record(conn, question=f"{f['path']}:{f['start_line']} 라우트 표를 해석하지 못함", domain="entrypoint",
                            scope=f"route-table:{f['path']}:{f['start_line']}", severity="medium", required_level=L0, origin="resolver")
            continue
        tables.append(table)
        table_feature[table.owner] = f["id"]
    stats["route_tables"] = len(tables)
    composed = routetable.compose(tables)
    for entry, full, assumptions in composed:
        if entry.kind != "http":
            unknowns.record(conn, question=f"{entry.file}:{entry.line} 라우트 표 항목 '{entry.path}' 을(를) 펼치지 못함 — "
                            + "; ".join(assumptions)[:200], domain="entrypoint", scope=f"route-entry:{entry.file}:{entry.line}:{entry.path}",
                            severity="medium", required_level=L0, origin="resolver")
            continue
        fid = _record_feature(conn, project, kind="http", name=full, framework="route-table-macro", path=entry.file,
                              line=entry.line, handler=entry.handler, method=entry.method, full_path=full,
                              assumptions=assumptions, derived_from="route-table", locs=[], source="resolver")
        if fid:
            stats["resolved_routes"] += 1
    for owner, fid in table_feature.items():
        conn.execute("UPDATE feature SET kind = 'route-table-template', full_path = ? WHERE id = ?", (owner, fid))


def _dynamic(conn, project: dict, repo: Path, functions, code_files: list[str], stats: dict) -> None:
    rows = conn.execute(
        "SELECT * FROM feature WHERE kind IN ('http','http-mount') AND in_test = 0 AND source = 'astgrep' "
        "AND framework != 'spring'").fetchall()
    for f in rows:
        expr = f["name"]
        if literal(expr.lstrip("&").strip()) is not None:
            continue
        lang = conn.execute("SELECT language FROM file WHERE path = ?", (f["path"],)).fetchone()["language"]
        resolver = Resolver(repo, lang or "", functions, _callers_factory(conn), code_files)
        scope_key = f"{f['path']}:{f['start_line']}"
        unk = conn.execute("SELECT id, revision FROM unknown WHERE domain = 'entrypoint' AND scope = ?", (scope_key,)).fetchone()
        try:
            values = resolver.resolve(expr, Scope(f["path"], f["start_line"], resolver._func_start(f["path"], f["start_line"])))
        except (Unresolved, re.error, IndexError, ValueError) as e:  # 해석기는 근사 — 실패해도 인벤토리는 계속
            stats["dynamic_failed"] += 1
            if unk:
                unknowns.update(conn, unk["id"], expected_revision=unk["revision"], actor="resolver",
                                note=f"정적 해석 실패: {e}")
            continue
        stats["dynamic_resolved"] += 1
        ev_ids, assumption_set, failures = [], set(), set()
        for v in values:
            handler = (v.extra.get("siblings") or [f["handler"]])[0] if v.extra.get("siblings") else f["handler"]
            item = v.extra.get("item_loc") or {"path": f["path"], "start_line": f["start_line"]}
            fid = _record_feature(
                conn, project, kind=f["kind"], name=v.text, framework=f["framework"], path=item["path"],
                line=item["start_line"], handler=handler, method=",".join(_methods_from_handler(handler)) or None,
                full_path=normalize_path(v.text), assumptions=v.assumptions, derived_from=f["id"],
                locs=v.locs + [{"path": f["path"], "start_line": f["start_line"], "end_line": f["start_line"]}], source="resolver",
            )
            if fid:
                stats["resolved_routes"] += 1
                ev_ids.append(conn.execute("SELECT evidence_id FROM feature WHERE id = ?", (fid,)).fetchone()[0])
            assumption_set.update(v.assumptions)
            failures.update(v.extra.get("partial_failures", []))
        conn.execute("UPDATE feature SET kind = ? WHERE id = ?", (f["kind"] + "-template", f["id"]))
        if unk:
            note = f"정적 해석으로 경로 {len(values)}개 펼침" + (" (설정 기본값 가정)" if assumption_set else "")
            unknowns.update(conn, unk["id"], expected_revision=unk["revision"], actor="resolver", status="investigating",
                            add_supporting=sorted(set(ev_ids))[:50], note=note)
        if assumption_set:
            unknowns.record(conn, question=f"{scope_key} 라우트 경로가 설정값에 좌우됨 — " + "; ".join(sorted(assumption_set))[:400],
                            domain="config", scope=f"route-config:{scope_key}", severity="low", required_level=L3,
                            supporting_evidence_ids=sorted(set(ev_ids))[:20], origin="resolver",
                            recommended_probes=[{"operation": "check_deploy_config", "rationale": "배포 환경별 설정값 확인 (.env 는 읽지 않음)"}])
        if failures:
            unknowns.record(conn, question=f"{scope_key} 의 일부 호출부를 해석하지 못함: " + "; ".join(sorted(failures))[:400],
                            domain="entrypoint", scope=f"route-partial:{scope_key}", severity="medium", required_level=L0, origin="resolver")


def _mounts(conn, functions, stats: dict) -> None:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for path, fns in functions.items():
        for fn in fns:
            by_name[fn["name"]].append({**fn, "path": path})
    for _ in range(3):  # 중첩 마운트
        changed = False
        for mount in conn.execute("SELECT * FROM feature WHERE kind = 'http-mount' AND in_test = 0").fetchall():
            prefix = literal(mount["name"].lstrip("&").strip())
            m = re.match(r"\s*(\w+)\s*\(", mount["handler"] or "")
            if prefix is None or not m or len(by_name.get(m.group(1), [])) != 1:
                continue
            fn = by_name[m.group(1)][0]
            children = conn.execute(
                "SELECT * FROM feature WHERE path = ? AND start_line BETWEEN ? AND ? AND kind IN ('http','http-mount') "
                "AND in_test = 0 AND (derived_from IS NULL OR derived_from NOT LIKE 'mount:%')",
                (fn["path"], fn["start_line"], fn["end_line"])).fetchall()
            for c in children:
                child = c["full_path"] or literal(c["name"].lstrip("&").strip())
                if child is None:
                    continue
                method = c["method"] or ",".join(_methods_from_handler(c["handler"])) or None
                conn.execute("UPDATE feature SET full_path = ?, method = ?, derived_from = ? WHERE id = ?",
                             (normalize_path(prefix + "/" + child), method, f"mount:{mount['id']}", c["id"]))
                stats["mounted"] += 1
                changed = True
        if not changed:
            break


def _spring(conn, project: dict, repo: Path, stats: dict) -> None:
    rows = conn.execute("SELECT * FROM feature WHERE framework = 'spring' AND kind = 'http' AND source = 'astgrep'").fetchall()
    if not rows:
        return
    lines_cache: dict[str, list[str]] = {}

    def lines(path: str) -> list[str]:
        if path not in lines_cache:
            lines_cache[path] = (repo / path).read_text(encoding="utf-8", errors="replace").splitlines()
        return lines_cache[path]

    parsed = []
    for f in rows:
        src = lines(f["path"])
        text = "\n".join(src[f["start_line"] - 1:f["start_line"] + 20])
        m = SPRING_RE.search(text)
        if not m:
            continue
        kind_name = m.group(1)
        args = ""
        after = text[m.end():]
        if after.lstrip().startswith("("):
            start = m.end() + (len(after) - len(after.lstrip()))
            args, _ = balanced(text, start)
        paths, method = [], None if kind_name == "Request" else kind_name.upper()
        for part in split_top(args):
            key, _, value = part.partition("=") if re.match(r"^\s*\w+\s*=", part) else ("value", "", part)
            key, value = key.strip(), value.strip()
            if key in ("value", "path"):
                items = split_top(value[1:-1]) if value.startswith("{") else [value]
                for it in items:
                    lit = literal(it)
                    paths.append(lit if lit is not None else "{" + it + "}")
            elif key == "method":
                found = re.findall(r"RequestMethod\.(\w+)", value)
                method = ",".join(found) or method
        # 어노테이션 다음 코드 줄이 class/interface 면 클래스 접두
        level = "method"
        for ln in range(f["start_line"], min(f["start_line"] + 15, len(src)) + 1):
            code = src[ln - 1].strip()
            if not code or code.startswith("@") or code.startswith("//") or code.startswith("*") or code.endswith(")") and code.startswith("\""):
                continue
            if re.search(r"\b(class|interface|record)\b", code):
                level = "class"
            break
        parsed.append({"row": f, "paths": paths or [""], "method": method, "level": level})

    class_prefix: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)
    for p in parsed:
        if p["level"] == "class":
            class_prefix[p["row"]["path"]].append((p["row"]["start_line"], p["paths"]))
            conn.execute("UPDATE feature SET kind = 'http-mount', full_path = ? WHERE id = ?",
                         (normalize_path(p["paths"][0]), p["row"]["id"]))
    for p in parsed:
        if p["level"] != "method":
            continue
        f = p["row"]
        prefixes = [""]
        candidates = [c for c in class_prefix.get(f["path"], []) if c[0] < f["start_line"]]
        if candidates:
            prefixes = max(candidates)[1]
            stats["spring_prefixed"] += 1
        fulls = [normalize_path("/" + pre + "/" + pth) for pre in prefixes for pth in p["paths"]]
        unresolved = [x for x in fulls if "{" in x and re.search(r"\{[A-Za-z_][\w.]*\}", x)]
        conn.execute("UPDATE feature SET full_path = ?, method = ?, assumptions = ? WHERE id = ?",
                     (fulls[0], p["method"] or "ANY", ledger.dumps(["여러 경로: " + ", ".join(fulls)] if len(fulls) > 1 else []), f["id"]))
        if unresolved:
            unknowns.record(conn, question=f"{f['path']}:{f['start_line']} 매핑 경로가 상수 {unresolved[0]} — 값 확인 필요",
                            domain="entrypoint", scope=f"spring-const:{f['path']}:{f['start_line']}", severity="medium",
                            required_level=L0, origin="resolver")
