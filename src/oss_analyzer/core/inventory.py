"""인벤토리 — 숨은 빈틈 0 의 출발점.

기준 목록은 엔진이 아니라 git 이다. 고정 커밋의 모든 파일을 분류하고, 구조 지도(codebase-memory)의
심볼을 단위에 전부 배정하고, 진입점을 규칙팩으로 수집한다. 엔진이 못 본 곳은 전부 미확인으로 남긴다.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from oss_analyzer.adapters import git
from oss_analyzer.adapters.astgrep import AstGrep, detect, is_literal, load_rulepacks
from oss_analyzer.adapters.cbm import CodebaseMemory
from oss_analyzer.adapters.lsp import language_available
from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import evidence, extpoints, ledger, mybatis, nextjs, routes, unknowns
from oss_analyzer.core import license as license_mod
from oss_analyzer.core.classify import ANALYZED_CATEGORIES, classify
from oss_analyzer.core.levels import L0, L1, LEVEL_NAMES, ceiling_for
from oss_analyzer.core.units import build_units, unit_for_symbol

PY_DECORATOR_RE = re.compile(
    r"""^@(\w+)\.(get|post|put|delete|patch|head|options|route|api_route|websocket)\s*\(\s*((?:[rbuf]?)(?:"[^"]*"|'[^']*'))?""", re.I)
NATIVE_BINARY_EXT ={".so", ".dll", ".dylib", ".exe", ".jar", ".war", ".class", ".wasm", ".a", ".lib", ".o", ".obj", ".node"}


def _read_meta(repo: Path, path: str) -> tuple[bool, int, str, int]:
    try:
        data = (repo / path).read_bytes()
    except OSError:
        return False, 0, "", 0
    is_binary = b"\0" in data[:8192]
    if is_binary:
        return True, 0, "", len(data)
    first = data[:200].split(b"\n", 1)[0].decode("utf-8", "replace")
    return False, data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1), first, len(data)


def detect_engines(settings: Settings, languages: set[str]) -> dict:
    from oss_analyzer.adapters.joern import Joern

    engines: dict[str, bool] = {"structure": CodebaseMemory(settings).available, "entrypoints": AstGrep(settings).available,
                                "joern": Joern(settings.home).available, "coverage": False}
    reasons: dict[str, str] = {}
    for lang in sorted(languages):
        ok, reason = language_available(lang)
        engines[f"lsp:{lang}"] = ok
        reasons[f"lsp:{lang}"] = reason
    return {"engines": engines, "reasons": reasons}


def build(conn, settings: Settings, project: dict, rebuild: bool = False) -> dict:
    if project["inventory_at"] and not rebuild:
        raise ToolError("invalid_request", "이미 인벤토리가 있음", recovery="진행 상황을 버리고 다시 만들려면 rebuild=true")
    if rebuild and conn.execute("SELECT COUNT(*) FROM unit WHERE status != 'pending'").fetchone()[0]:
        raise ToolError("invalid_request", "진행 중이거나 완료된 단위가 있어 다시 만들 수 없음 (증분 재분석은 3단계 기능)")

    repo = Path(project["repo_dir"])
    commit = project["commit_sha"]
    cbm = CodebaseMemory(settings)
    if not cbm.available:
        raise ToolError("capability_unavailable", "구조 지도 엔진(codebase-memory-mcp)이 필요함", recovery="oss_doctor 실행")

    manual = {r["path"]: (r["category"], r["language"], r["reason"])
              for r in conn.execute("SELECT * FROM file WHERE reason LIKE 'manual:%'").fetchall()}
    blob = git.blob_shas(repo, commit)

    # 1) 파일 전수 분류
    files = []
    for path in git.ls_files(repo, commit):
        is_binary, lines, first_line, size = _read_meta(repo, path)
        category, language, reason = manual.get(path) or classify(path, is_binary, first_line)
        files.append({"path": path, "category": category, "language": language, "lines": lines,
                      "sha": blob.get(path), "reason": reason, "chars": size})
    languages = {f["language"] for f in files if f["language"] and f["category"] in ANALYZED_CATEGORIES}
    caps = detect_engines(settings, languages)

    with ledger.tx(conn):
        for table in ("file", "unit", "symbol", "feature"):
            conn.execute(f"DELETE FROM {table}")
        # 인벤토리·해석기가 만든 미확인은 다시 만들 때 새로 계산 (에이전트가 남긴 것은 유지)
        conn.execute("DELETE FROM unknown WHERE origin IN ('inventory', 'resolver')")
        conn.executemany(
            "INSERT INTO file (path, category, language, lines, sha, reason) VALUES (:path, :category, :language, :lines, :sha, :reason)",
            [{k: f[k] for k in ("path", "category", "language", "lines", "sha", "reason")} for f in files],
        )

    # 2) 구조 지도 — 비밀 파일은 엔진 캐시에도 들어가지 않도록 인덱싱 전에 제외
    secrets = [f["path"] for f in files if f["category"] == "secret"]
    index = cbm.index(repo, project["id"], exclude=secrets)
    by_path = {f["path"]: f for f in files}
    edges = [e for e in cbm.all_calls(project["id"])
             if e["caller_path"] in by_path and by_path[e["caller_path"]]["category"] in ANALYZED_CATEGORIES]
    with ledger.tx(conn):
        conn.execute("DELETE FROM call_edge")
        conn.executemany(
            "INSERT INTO call_edge (caller, caller_path, caller_line, callee, callee_path, callee_line, call_line, confidence, strategy) "
            "VALUES (:caller, :caller_path, :caller_line, :callee, :callee_path, :callee_line, :call_line, :confidence, :strategy)",
            edges,
        )
    # 필수 검토 심볼은 코드·테스트 파일의 것만 — XML/YAML/POM 요소(groupId, if, select…)는 태그 이름이라 의미가 없다.
    # 대신 MyBatis 매퍼 XML 은 SQL 구문을 직접 심볼로 뽑고 Java 매퍼 메서드와 연결한다.
    all_symbols = cbm.symbols(project["id"])
    symbols = [s for s in all_symbols if s["path"] in by_path and by_path[s["path"]]["category"] in ("code", "test")]
    java_methods = [s for s in symbols if s["label"] in ("Method", "Function")]
    mybatis_edges = []
    for f in files:
        if f["category"] == "asset" and f["path"].lower().endswith(".xml"):
            text = evidence.decode((repo / f["path"]).read_bytes())
            if mybatis.is_mapper(text):
                namespace, stmts = mybatis.statements(f["path"], text)
                symbols.extend(stmts)
                mybatis_edges.extend(mybatis.link_edges(namespace, stmts, java_methods))
    if mybatis_edges:
        with ledger.tx(conn):
            conn.executemany(
                "INSERT INTO call_edge (caller, caller_path, caller_line, callee, callee_path, callee_line, call_line, confidence, strategy) "
                "VALUES (:caller, :caller_path, :caller_line, :callee, :callee_path, :callee_line, :call_line, :confidence, :strategy)",
                mybatis_edges,
            )
    symbols_by_path: dict[str, list[dict]] = defaultdict(list)
    for s in symbols:
        symbols_by_path[s["path"]].append(s)

    # 3) 단위 분할 + 심볼 배정
    units = build_units(files, symbols_by_path, settings.unit_max_lines, settings.unit_max_chars)
    units_by_path: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for u in units:
        for m in u["members"]:
            units_by_path[m["path"]].append((u, m))
        langs = {by_path[m["path"]]["language"] for m in u["members"]}
        ceilings = [ceiling_for(lang, caps["engines"]) for lang in langs] or [L0]
        u["ceiling_level"] = min(ceilings)
        # 목표: 코드 단위는 L1(호출 관계를 LSP 로 확인), 자산 단위는 L0. L2·L3 는 필요한 결론에만 선택적으로
        u["target_level"] = min(u["ceiling_level"], L1) if u["kind"] in ("code", "test") else L0

    with ledger.tx(conn):
        conn.executemany(
            "INSERT INTO unit (id, kind, title, members, line_count, target_level, ceiling_level) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(u["id"], u["kind"], u["title"], ledger.dumps(u["members"]), u["line_count"], u["target_level"], u["ceiling_level"]) for u in units],
        )
        seen_ids = set()
        rows = []
        for s in symbols:
            sid = "s_" + hashlib.sha256(f"{s['qualified_name']}|{s['path']}|{s['start_line']}".encode()).hexdigest()[:16]
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            rows.append((sid, s["qualified_name"], s["name"], s["label"], s["path"], s["start_line"], s["end_line"],
                         unit_for_symbol(units_by_path, s)))
        conn.executemany(
            "INSERT INTO symbol (id, qualified_name, name, label, path, start_line, end_line, unit_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        # 4) 엔진이 못 본 곳 → 미확인
        _structure_unknowns(conn, index, by_path)
        for f in files:
            if f["category"] == "binary" and Path(f["path"]).suffix.lower() in NATIVE_BINARY_EXT:
                unknowns.record(conn, question=f"소스 없는 바이너리 {f['path']} 의 역할·동작", domain="binary", scope=f["path"],
                                severity="high", required_level=L0, origin="inventory",
                                recommended_probes=[{"operation": "build_script_trace", "rationale": "빌드 스크립트·로더 코드에서 사용처 추적"},
                                                    {"operation": "rea_handoff", "rationale": "바이너리 분석 도구로 넘김"}])
        for lang in sorted(languages):
            if not caps["engines"].get(f"lsp:{lang}"):
                unknowns.record(conn, question=f"{lang} 코드는 LSP 를 쓸 수 없어 호출 관계를 정밀 검증할 수 없음 ({caps['reasons'].get(f'lsp:{lang}')})",
                                domain="capability", scope=f"lang:{lang}", severity="medium", required_level=L1, origin="inventory",
                                recommended_probes=[{"operation": "oss_doctor", "rationale": "언어 서버 설치"}])

    # 5) 진입점 — 규칙팩 → 프레임워크 규약(Next.js) → 동적 경로 해석·조합
    feature_stats = _entrypoints(conn, settings, project, files)
    feature_stats["nextjs"] = _nextjs(conn, project, files)
    feature_stats["routes"] = routes.post_process(conn, project, files)
    feature_stats["extension_points"] = extpoints.build(conn, project, files)
    feature_stats["license"] = {k: v for k, v in license_mod.scan(conn, project).items() if k != "note"}

    with ledger.tx(conn):
        conn.execute("UPDATE project SET inventory_at = ?, capabilities = ?",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), ledger.dumps(caps)))

    by_category: dict[str, int] = defaultdict(int)
    for f in files:
        by_category[f["category"]] += 1
    unassigned = conn.execute("SELECT COUNT(*) FROM symbol WHERE unit_id IS NULL").fetchone()[0]
    return {
        "files": len(files), "by_category": dict(by_category), "languages": sorted(languages),
        "units": len(units), "symbols": len(rows), "unassigned_symbols": unassigned,
        "features": feature_stats, "capabilities": caps,
        "unit_ceiling": {LEVEL_NAMES[k]: v for k, v in _count_ceilings(units).items()},
        "unknowns_open": conn.execute("SELECT COUNT(*) FROM unknown WHERE status != 'resolved'").fetchone()[0],
    }


def _count_ceilings(units: list[dict]) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    for u in units:
        out[u["ceiling_level"]] += 1
    return dict(out)


def _structure_unknowns(conn, status: dict, by_path: dict) -> None:
    """index_status(diagnostics=full) 의 파싱 품질 신호 → 미확인. 분석 대상 파일만."""
    messages = {
        "parse_partial": lambda i: f"구조 지도가 {i['path']} 의 {i.get('error_ranges')} 줄을 파싱하지 못함 — 심볼 누락 가능",
        "parse_unusable": lambda i: f"구조 지도가 {i['path']} 전체를 파싱하지 못함",
        "skipped": lambda i: f"구조 지도가 {i['path']} 를 인덱싱하지 않음 ({i.get('reason', '사유 없음')})",
        "not_indexed": lambda i: f"구조 지도가 {i['path']} 를 의도적으로 제외함 ({i.get('reason', 'ignore 규칙')}) — 심볼 없음",
    }
    for key, message in messages.items():
        block = status.get(key) or {}
        for item in block.get("files", []):
            f = by_path.get(item.get("path"))
            # 심볼 검토 대상은 코드·테스트뿐 — 자산(SVG·Dockerfile 등)을 구조 지도가 건너뛴 것은 빈틈이 아님
            if f and f["category"] in ("code", "test"):
                unknowns.record(conn, question=message(item), domain="structure", scope=f"{key}:{item['path']}",
                                severity="medium", required_level=L0, origin="inventory",
                                recommended_probes=[{"operation": "read_source", "rationale": "해당 부분을 직접 읽고 누락 심볼 확인"}])
        if block.get("truncated"):
            unknowns.record(conn, question=f"구조 지도 보고서({key})가 잘려서 일부 파일 목록이 빠짐",
                            domain="structure", scope=key, severity="high", required_level=L0, origin="inventory",
                            recommended_probes=[{"operation": "index_status", "rationale": "diagnostics=full 로 다시 조회"}])


def _nextjs(conn, project: dict, files: list[dict]) -> dict | None:
    repo = Path(project["repo_dir"])
    paths = [f["path"] for f in files if f["category"] in ANALYZED_CATEGORIES]
    if not nextjs.is_next_project(repo, [f["path"] for f in files]):
        return None
    found = nextjs.scan(repo, paths)
    counts: dict[str, int] = defaultdict(int)
    with ledger.tx(conn):
        for f in found["features"]:
            kind = ("inactive-" if f["inactive"] else "") + f["kind"]
            routes._record_feature(conn, project, kind=kind, name=f["name"], framework="nextjs", path=f["path"],
                                   line=f["line"], handler=None, method=f["method"], full_path=f["name"],
                                   assumptions=f["notes"], derived_from=None, locs=[], source="convention")
            counts[kind] += 1
        for u in found["unknowns"]:
            unknowns.record(conn, origin="inventory", **u)
    return dict(counts)


def _entrypoints(conn, settings: Settings, project: dict, files: list[dict]) -> dict:
    repo = Path(project["repo_dir"])
    paths = [f["path"] for f in files]
    analyzed = {f["path"] for f in files if f["category"] in ANALYZED_CATEGORIES}
    by_category = {f["path"]: f["category"] for f in files}
    rules, rule_meta = [], {}
    detected, undetected_frameworks = [], []
    extra = [settings.home / "rulepacks", repo.parent / "rulepacks"]
    if os.environ.get("OSS_ANALYZER_RULEPACKS"):
        extra.append(Path(os.environ["OSS_ANALYZER_RULEPACKS"]))
    for pack_file in load_rulepacks(extra):
        for pack in pack_file.get("packs", []):
            if detect(pack["detect"], repo, paths):
                detected.append(pack["framework"])
                for r in pack["rules"]:
                    rules.append(r)
                    rule_meta[r["id"]] = {**r, "framework": pack["framework"]}
        for spec in pack_file.get("detect_only", []):
            if detect(spec, repo, paths):
                undetected_frameworks.append(spec["framework"])

    with ledger.tx(conn):
        for fw in undetected_frameworks:
            unknowns.record(conn, question=f"{fw} 프레임워크가 감지됐지만 진입점 규칙팩이 없음 — 진입점 누락 가능",
                            domain="entrypoint", scope=f"framework:{fw}", severity="high", required_level=L0, origin="inventory",
                            recommended_probes=[{"operation": "add_rulepack", "rationale": f"rulepacks/ 에 {fw} 규칙 추가"}])

    if not AstGrep(settings).available:
        return {"frameworks": detected, "missing_rulepacks": undetected_frameworks, "count": 0, "error": "ast-grep 없음"}

    matches = AstGrep(settings).scan(repo, rules)
    test_regions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for m in matches:
        if rule_meta[m["rule_id"]]["kind"] == "test-region":
            test_regions[m["path"]].append((m["start_line"], m["end_line"]))

    def in_test(path: str, line: int) -> bool:
        return any(a <= line <= b for a, b in test_regions.get(path, []))

    count, dynamic, test_count = 0, 0, 0
    with ledger.tx(conn):
        for m in matches:
            if m["path"] not in analyzed:
                continue
            meta = rule_meta[m["rule_id"]]
            if meta["kind"] == "test-region":
                continue
            name_var = meta.get("name")
            name_info = m["vars"].get(name_var) if name_var else None
            name = name_info["text"] if name_info else m["text"].splitlines()[0].strip()[:160]
            line = name_info["line"] if name_info else m["start_line"]
            line_end = m["end_line"] if meta.get("extract") else line  # 표·속성 해석기는 전체 범위가 필요
            if meta.get("extract") == "attr_path":
                am = re.match(r'^#\[(?:actix_web::)?(\w+)\s*\(\s*"([^"]*)"', m["text"])
                if am:
                    name = f'"{am.group(2)}"'
            method = (m["vars"].get(meta.get("method", ""), {}).get("text") or "").upper() or None
            receiver = m["vars"].get(meta.get("receiver", ""), {}).get("text")
            if meta.get("extract") == "py_decorator":
                dm = PY_DECORATOR_RE.match(m["text"])
                if dm:
                    receiver, verb, path_lit = dm.group(1), dm.group(2).lower(), dm.group(3)
                    name = path_lit or name
                    if verb in ("route", "api_route"):
                        methods = re.findall(r"""["'](GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)["']""", m["text"], re.I)
                        method = ",".join(sorted({x.upper() for x in methods})) or "GET"  # Flask 기본 GET
                    elif verb == "websocket":
                        method = "WS"
                    else:
                        method = verb.upper()
            handler = m["vars"].get(meta.get("handler", ""), {}).get("text") or m["multi"].get(meta.get("handler", ""))
            loc = {"path": m["path"], "start_line": line, "end_line": line}
            ev = evidence.record(conn, project, provider="astgrep", operation=f"rule:{m['rule_id']}",
                                 parameters={"rule_id": m["rule_id"]}, result={"name": name, "handler": handler},
                                 locations=[loc], confidence="observed")
            fid = "f_" + ev["evidence_id"][3:]
            # 테스트·예제·벤치 코드의 진입점은 제품 기능이 아니므로 구분 (카탈로그에서 in_test 로 표시)
            testy = (in_test(m["path"], line) or by_category.get(m["path"]) == "test"
                     or bool({"examples", "example", "benches", "bench"} & set(Path(m["path"]).parts)))
            conn.execute(
                "INSERT OR IGNORE INTO feature (id, kind, name, framework, path, start_line, end_line, handler, source, evidence_id, in_test) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'astgrep', ?, ?)",
                (fid, meta["kind"], name, meta["framework"], m["path"], line, line_end, handler, ev["evidence_id"], int(testy)),
            )
            if meta.get("extract") == "attr_path":
                method = m["text"].split("(")[0].split("::")[-1].lstrip("#[").upper()
            if method or receiver:
                conn.execute("UPDATE feature SET method = COALESCE(?, method), receiver = ? WHERE id = ?", (method, receiver, fid))
            count += 1
            if testy:
                test_count += 1
                continue
            if name_var and name_info and not is_literal(name_info["text"]):
                dynamic += 1
                unknowns.record(conn, question=f"{m['path']}:{line} 의 진입점 경로가 동적으로 만들어짐({name[:80]}) — 실제 경로 목록 확인 필요",
                                domain="entrypoint", scope=f"{m['path']}:{line}", severity="medium", required_level=L1,
                                supporting_evidence_ids=[ev["evidence_id"]], origin="inventory",
                                recommended_probes=[{"operation": "trace_value", "rationale": "경로 값을 만드는 코드를 따라가 목록화"},
                                                    {"operation": "runtime", "rationale": "실행 시 등록된 라우트 목록 덤프 (L3)"}])
    return {"frameworks": detected, "missing_rulepacks": undetected_frameworks, "count": count,
            "in_test": test_count, "dynamic": dynamic,
            "test_regions": sum(len(v) for v in test_regions.values())}
