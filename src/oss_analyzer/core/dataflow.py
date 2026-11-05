"""데이터 흐름 질의(L2) — "이 함수의 입력이 이 이름의 호출까지 흘러가는가" 를 Joern 으로 확인하고 근거로 남긴다.

질의: {"path": 소스 파일, "line": 함수 정의 줄 또는 호출 줄, "sink": 도착 호출 이름 정규식}
결과 경로마다 지나가는 위치(파일:줄)를 근거 위치로 기록한다. 흐름이 없으면 '없음'도 근거가 되지만
정적 데이터 흐름은 과소 근사일 수 있어(동적 디스패치·리플렉션·외부 라이브러리) 한계를 함께 적는다.
"""
from __future__ import annotations

import hashlib
import shutil
import time
from collections import Counter
from pathlib import Path

from oss_analyzer.adapters.joern import Joern
from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import evidence, ledger
from oss_analyzer.core.levels import DATAFLOW_LANGUAGES, EXPERIMENTAL_DATAFLOW
from oss_analyzer.core.project import workspace

MAX_LOCS = 60


def _primary_language(conn, language: str | None) -> str:
    if language:
        return language
    rows = conn.execute("SELECT language, SUM(lines) n FROM file WHERE category = 'code' AND language IS NOT NULL "
                        "GROUP BY language ORDER BY n DESC").fetchall()
    for r in rows:
        if r["language"] in DATAFLOW_LANGUAGES:
            return r["language"]
    raise ToolError("capability_unavailable", "데이터 흐름을 지원하는 언어의 코드가 없음")


# 데이터 흐름 대상에서 빼는 범주 — 테스트·테스트 데이터·외부 코드는 CPG 를 키우기만 하고 질의 대상이 아님
EXCLUDED_CATEGORIES = ("test", "test-data", "vendored", "generated", "doc", "asset", "secret", "binary", "unclassified")


def selection(conn, scope: list[str] | None = None) -> tuple[list[str], int]:
    """CPG 에 넣을 파일 (포함 목록, 제외 수). scope 를 주면 그 경로 접두 밖도 뺀다."""
    prefixes = [s.strip("/").replace("\\", "/") + "/" for s in scope or []]
    keep, dropped = [], 0
    for r in conn.execute("SELECT path, category FROM file ORDER BY path").fetchall():
        if r["category"] in EXCLUDED_CATEGORIES or (prefixes and not any(r["path"].startswith(p) for p in prefixes)):
            dropped += 1
        else:
            keep.append(r["path"])
    return keep, dropped


def cpg_path(settings: Settings, project: dict, language: str, key: str = "") -> Path:
    return workspace(settings, project["id"]) / "cpg" / f"{language}{'-' + key if key else ''}.bin"


def build(conn, settings: Settings, project: dict, language: str | None = None, rebuild: bool = False,
          scope: list[str] | None = None) -> dict:
    """제외할 파일이 있으면 포함 파일만 복사한 트리(내용·줄 번호 동일)를 만들어 그걸 파싱한다.

    joern-parse 의 --exclude-regex 는 Windows 에서 .bat 을 두 번 거치며 | , = 가 깨진다 (fixture 검증에서 실제 발생).
    복사본은 CPG 와 함께 보관한다 — 결과 경로를 저장소 상대 경로로 되돌릴 때 기준이 된다.
    """
    lang = _primary_language(conn, language)
    keep, dropped = selection(conn, scope)
    repo = Path(project["repo_dir"])
    key = hashlib.sha256("\n".join(keep).encode()).hexdigest()[:10] if dropped else ""
    out = cpg_path(settings, project, lang, key)
    source = out.parent / f"src-{key}" if key else repo
    info = {"language": lang, "cpg": str(out), "source_root": str(source), "files": len(keep), "excluded_files": dropped,
            "scope": scope or None}
    if out.exists() and source.exists() and not rebuild:
        return {**info, "cached": True}
    if key:
        if source.exists():
            shutil.rmtree(source)
        for rel in keep:
            dst = source / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / rel, dst)
    started = time.monotonic()
    Joern(settings.home).build_cpg(source, lang, out)
    return {**info, "cached": False, "seconds": round(time.monotonic() - started, 1)}


def query(conn, settings: Settings, project: dict, queries: list[dict], language: str | None = None,
          scope: list[str] | None = None) -> dict:
    lang = _primary_language(conn, language)
    built = build(conn, settings, project, lang, scope=scope)
    raw = Joern(settings.home).run_queries(Path(built["cpg"]), queries)
    repo = Path(built["source_root"]).resolve().as_posix().lower()
    results = []
    for item in raw:
        q = item["query"]
        flows = item.get("flows", [])
        locs, seen = [], set()
        for flow in flows:
            for step in flow:
                path = _relative(step["file"], repo)
                if path is None or step["line"] < 1 or (path, step["line"]) in seen:
                    continue
                seen.add((path, step["line"]))
                try:
                    locs.append(evidence.validate_location(conn, project, {"path": path, "start_line": step["line"], "end_line": step["line"]}))
                except ToolError:
                    continue
                if len(locs) >= MAX_LOCS:
                    break
        limitations = ["정적 데이터 흐름은 동적 디스패치·리플렉션·외부 라이브러리 내부를 따라가지 못할 수 있음"]
        if lang in EXPERIMENTAL_DATAFLOW:
            limitations.append(f"{lang} 데이터 흐름 프론트엔드는 실험적 — 결과를 코드로 재확인 필요")
        if item.get("sources", 0) == 0:
            limitations.append("소스 위치에서 함수·호출을 찾지 못함 (줄 번호 확인)")
        if item.get("sinks", 0) == 0:
            limitations.append(f"sink 정규식 '{q['sink']}' 에 맞는 호출이 없음")
        ev_id = None
        if locs or flows == []:
            anchor = locs or [evidence.validate_location(conn, project, {"path": q["path"], "start_line": q["line"], "end_line": q["line"]})]
            with ledger.tx(conn):
                ev = evidence.record(conn, project, provider="joern", operation="reachable_by_flows",
                                     parameters={k: q[k] for k in ("path", "line", "sink")},
                                     result={"flows": len(flows), "sources": item.get("sources"), "sinks": item.get("sinks")},
                                     locations=anchor, confidence="experimental" if lang in EXPERIMENTAL_DATAFLOW else "derived",
                                     limitations=limitations, validated=True)
            ev_id = ev["evidence_id"]
        sinks_hit = Counter(flow[-1]["code"][:80] for flow in flows if flow)
        results.append({"query": q, "flows": len(flows), "evidence_id": ev_id, "level": "L2",
                        "sinks_reached": sinks_hit.most_common(10), "limitations": limitations,
                        "paths": [[f"{_relative(s['file'], repo)}:{s['line']} {s['code'][:80]}" for s in flow] for flow in flows[:5]]})
    return {"language": lang, "results": results}


def _relative(file: str, repo: str) -> str | None:
    f = file.replace("\\", "/")
    low = f.lower()
    if low.startswith(repo):
        return f[len(repo):].lstrip("/")
    if not (len(f) > 1 and f[1] == ":") and not f.startswith("/"):
        return f  # 이미 상대 경로
    return None
