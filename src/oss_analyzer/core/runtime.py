"""실행 검증(L3) — 테스트·실행을 돌려 얻은 커버리지 리포트를 심볼 단위 실행 근거로 바꾼다.

지원 형식 (자동 판별):
  coverage.py JSON (`coverage json`)       files.{path}.executed_lines
  LCOV (`--coverage` 류, c8, cargo-llvm-cov, go 변환 등)  SF / DA / FN / FNDA
  Istanbul JSON (nyc·c8 `coverage-final.json`)          statementMap + s, fnMap + f
  Cobertura XML / JaCoCo XML                           <line number hits> / <line nr ci>

심볼 판정: 함수 hit 수가 있으면(FNDA·Istanbul f) 그것을, 없으면 '정의 줄을 뺀 본문 줄' 중 실행된 줄이 있는지로 본다.
(Python 은 import 시 def 줄이 실행되므로 정의 줄만 실행된 함수는 '호출됨'이 아니다.)

실행기: 남의 코드를 돌리는 것이라 격리가 기본이다.
  Docker 데몬이 있으면 네트워크 차단 컨테이너에서 저장소 사본으로 실행
  없으면 --trusted(우리가 만든 코드 등 신뢰하는 저장소)일 때만 로컬 임시 사본에서 실행
  분석 전용 clone 은 절대 쓰지 않는다 (고정 커밋 유지).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import container, evidence, ledger

MAX_EVIDENCE = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 파서 → {경로: {"lines": {줄: 횟수}, "functions": [(이름, 줄, 횟수)]}}

def _blank() -> dict:
    return {"lines": {}, "functions": []}


def parse(text: str) -> tuple[str, dict[str, dict]]:
    s = text.lstrip()
    if s.startswith("{"):
        data = json.loads(s)
        if isinstance(data.get("files"), dict) and "meta" in data:
            return "coverage.py", _coveragepy(data)
        return "istanbul", _istanbul(data)
    if s.startswith("<"):
        return _xml(s)
    if "SF:" in text:
        return "lcov", _lcov(text)
    raise ToolError("invalid_request", "커버리지 형식을 알 수 없음 (coverage.py JSON / LCOV / Istanbul JSON / Cobertura·JaCoCo XML)")


def _coveragepy(data: dict) -> dict[str, dict]:
    out = {}
    for path, f in data["files"].items():
        d = _blank()
        for ln in f.get("executed_lines", []):
            d["lines"][int(ln)] = 1
        for ln in f.get("missing_lines", []):
            d["lines"].setdefault(int(ln), 0)
        out[path] = d
    return out


def _lcov(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    cur, fn_line = None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            cur, fn_line = _blank(), {}
            out[line[3:]] = cur
        elif cur is None:
            continue
        elif line.startswith("DA:"):
            ln, hits = line[3:].split(",")[:2]
            cur["lines"][int(ln)] = cur["lines"].get(int(ln), 0) + int(float(hits))
        elif line.startswith("FN:"):
            ln, name = line[3:].split(",", 1)
            fn_line[name] = int(ln)
        elif line.startswith("FNDA:"):
            hits, name = line[5:].split(",", 1)
            cur["functions"].append((name, fn_line.get(name, 0), int(float(hits))))
        elif line == "end_of_record":
            cur = None
    return out


def _istanbul(data: dict) -> dict[str, dict]:
    out = {}
    for key, f in data.items():
        if not isinstance(f, dict) or "statementMap" not in f:
            continue
        d = _blank()
        for sid, loc in f["statementMap"].items():
            hits = int(f.get("s", {}).get(sid, 0))
            for ln in range(loc["start"]["line"], loc["end"]["line"] + 1):
                d["lines"][ln] = max(d["lines"].get(ln, 0), hits)
        for fid, fn in f.get("fnMap", {}).items():
            d["functions"].append((fn.get("name", fid), fn["decl"]["start"]["line"] if "decl" in fn else fn["loc"]["start"]["line"],
                                   int(f.get("f", {}).get(fid, 0))))
        out[f.get("path", key)] = d
    return out


def _xml(text: str) -> tuple[str, dict[str, dict]]:
    # 리포트는 남의 코드가 만든 파일 — 엔티티 선언(billion laughs·외부 엔티티)은 받지 않는다.
    # Cobertura·JaCoCo 는 DOCTYPE 만 쓰고 ENTITY 는 쓰지 않으며, ElementTree 는 외부 DTD 를 가져오지 않는다.
    if re.search(r"<!ENTITY", text, re.I):
        raise ToolError("invalid_request", "XML 리포트에 엔티티 선언이 있어 거부함")
    root = ET.fromstring(text)
    out: dict[str, dict] = defaultdict(_blank)
    if root.tag == "report":  # JaCoCo
        for pkg in root.iter("package"):
            for sf in pkg.iter("sourcefile"):
                path = f"{pkg.get('name')}/{sf.get('name')}"
                for ln in sf.iter("line"):
                    out[path]["lines"][int(ln.get("nr"))] = int(ln.get("ci", 0))
        return "jacoco", dict(out)
    for cls in root.iter("class"):  # Cobertura
        path = cls.get("filename")
        for ln in cls.iter("line"):
            out[path]["lines"][int(ln.get("number"))] = int(ln.get("hits", 0))
    return "cobertura", dict(out)


# ---------------------------------------------------------------- 경로 맞추기

def _match_paths(report_paths: list[str], repo_paths: set[str], roots: list[str]) -> tuple[dict[str, str], list[str]]:
    """리포트 경로 → 저장소 상대 경로. 절대 경로는 roots 기준으로 자르고, 그래도 안 맞으면 꼬리 일치(유일할 때만)."""
    by_tail: dict[str, list[str]] = defaultdict(list)
    for p in repo_paths:
        parts = p.split("/")
        for i in range(len(parts)):
            by_tail["/".join(parts[i:])].append(p)
    norm_roots = [r.replace("\\", "/").rstrip("/").lower() + "/" for r in roots if r]
    mapping, unmatched = {}, []
    for rp in report_paths:
        p = rp.replace("\\", "/")
        low = p.lower()
        for r in norm_roots:
            if low.startswith(r):
                p = p[len(r):]
                break
        p = p.removeprefix("./")
        if p in repo_paths:
            mapping[rp] = p
            continue
        cands = by_tail.get(p) or by_tail.get("/".join(PurePosixPath(p).parts[-3:])) or []
        if len(cands) == 1:
            mapping[rp] = cands[0]
        else:
            unmatched.append(rp)
    return mapping, unmatched


# ---------------------------------------------------------------- 적재

def ingest(conn, proj: dict, report: Path, *, tool: str | None = None, roots: list[str] | None = None,
           command: str | None = None, run_commit: str | None = None) -> dict:
    text = report.read_text(encoding="utf-8", errors="replace")
    fmt, data = parse(text)
    if run_commit and run_commit != proj["commit_sha"]:
        raise ToolError("invalid_request", f"커버리지를 만든 커밋({run_commit[:12]})이 프로젝트 고정 커밋과 다름",
                        recovery="같은 커밋에서 다시 실행")
    files = {r["path"]: r["category"] for r in conn.execute("SELECT path, category FROM file").fetchall()}
    mapping, unmatched = _match_paths(list(data), set(files), (roots or []) + [proj["repo_dir"]])
    run_id = "cov_" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]

    symbols = defaultdict(list)
    for s in conn.execute("SELECT id, name, label, path, start_line, end_line FROM symbol").fetchall():
        symbols[s["path"]].append(dict(s))

    hits, misses, ev_count = [], [], 0
    rows = []
    with ledger.tx(conn):
        for rp, path in mapping.items():
            if files.get(path) == "secret":
                continue
            d = data[rp]
            fn_hits = {(ln, name.split(".")[-1].split("::")[-1]): n for name, ln, n in d["functions"]}
            for s in symbols.get(path, []):
                if s["label"] in ("Class", "Module", "Interface", "Trait", "Struct", "Type"):
                    continue
                body = [ln for ln in range(s["start_line"] + 1, s["end_line"] + 1) if ln in d["lines"]]
                executed = [ln for ln in body if d["lines"][ln] > 0]
                fn = next((n for (ln, name), n in fn_hits.items() if name == s["name"] and abs(ln - s["start_line"]) <= 3), None)
                if fn is not None:
                    called, basis = fn > 0, "function-hits"
                elif body:
                    called, basis = bool(executed), "body-lines"
                else:
                    continue  # 측정 줄이 없음 (한 줄 함수·선언) — 판정 안 함
                entry = {"symbol_id": s["id"], "name": s["name"], "path": path, "basis": basis,
                         "lines_hit": len(executed), "lines_measured": len(body)}
                if not called:
                    misses.append(entry)
                    continue
                ev_id = None
                if ev_count < MAX_EVIDENCE:
                    lo, hi = (min(executed), max(executed)) if executed else (s["start_line"], s["start_line"])
                    try:
                        ev = evidence.record(conn, proj, provider="coverage", operation="executed",
                                             parameters={"run_id": run_id, "symbol_id": s["id"]},
                                             result={"lines_hit": len(executed), "lines_measured": len(body), "basis": basis},
                                             locations=[{"path": path, "start_line": lo, "end_line": hi}], confidence="observed",
                                             limitations=["실행됐다는 사실만 증명 — 어떤 입력·분기로 실행됐는지는 별도 확인"])
                        ev_id = ev["evidence_id"]
                        ev_count += 1
                    except ToolError:
                        pass
                entry["evidence_id"] = ev_id
                hits.append(entry)
                rows.append((run_id, s["id"], ev_id, len(executed), len(body)))
        conn.execute("INSERT OR REPLACE INTO coverage_run (id, tool, format, commit_sha, command, report_path, files_matched, "
                     "files_unmatched, symbols_hit, symbols_missed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (run_id, tool or fmt, fmt, proj["commit_sha"], command, str(report), len(mapping), len(unmatched),
                      len(hits), len(misses), _now()))
        conn.execute("DELETE FROM coverage_hit WHERE run_id = ?", (run_id,))
        conn.executemany("INSERT INTO coverage_hit (run_id, symbol_id, evidence_id, lines_hit, lines_measured) VALUES (?, ?, ?, ?, ?)", rows)

    code_files = {p for p, c in files.items() if c == "code"}
    covered_files = {mapping[k] for k in mapping}
    return {
        "run_id": run_id, "format": fmt, "files_matched": len(mapping), "files_unmatched": unmatched[:50],
        "symbols_hit": len(hits), "symbols_not_hit": len(misses),
        "hits": hits[:200], "not_hit": misses[:200],
        "code_files_without_coverage": sorted(code_files - covered_files)[:200],
        "note": ("hit 의 evidence_id 는 L3 근거 — 미확인(required_level L3) 해결이나 결론 근거로 인용. "
                 "not_hit 은 '이번 실행에서 안 돈 것'이지 죽은 코드라는 증명이 아님"),
    }


def runs(conn) -> dict:
    return {"items": ledger.rows_to_dicts(conn.execute("SELECT * FROM coverage_run ORDER BY created_at DESC").fetchall())}


def symbol_runtime(conn, symbol_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute(f"SELECT symbol_id, evidence_id FROM coverage_hit WHERE symbol_id IN ({','.join('?' * len(symbol_ids))})",
                          symbol_ids).fetchall():
        if r["evidence_id"]:
            out[r["symbol_id"]].append(r["evidence_id"])
    return dict(out)


# ---------------------------------------------------------------- 실행기

SECRET_ENV_RE = re.compile(r"(?i)token|secret|passw|key|credential|cookie")


def safe_env(extra: dict | None = None) -> dict:
    """남의 코드·사용자 명령에 넘기는 환경 변수 — 토큰·키·비밀번호류 이름은 뺀다."""
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV_RE.search(k)}
    env.update(extra or {})
    return env


def docker_available() -> bool:
    return container.detect() is not None


def run(conn, proj: dict, settings, command: str, *, report: str, trusted: bool = False, image: str | None = None,
        timeout: int = 1800, network: bool = False) -> dict:
    """저장소 사본에서 command 를 실행하고 report(사본 기준 상대 경로)의 커버리지를 적재한다.

    Docker 가 있으면 컨테이너(기본 네트워크 차단), 없으면 trusted=True 일 때만 로컬.
    """
    repo = Path(proj["repo_dir"])
    work_root = settings.home / "runs" / proj["id"]
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=work_root))
    copy = work / "src"
    # 고정 커밋 내용만 복사 (.git 제외 → 실행 코드가 원장·clone 을 건드릴 수 없음)
    shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))
    eng = container.detect()
    if eng:
        if not image:
            raise ToolError("invalid_request", "Docker 실행에는 image 가 필요함 (예: python:3.11-slim)")
        argv = eng.argv("run", "--rm", "-v", f"{eng.path(copy)}:/work", "-w", "/work")
        if not network:
            argv += ["--network", "none"]
        argv += [image, "sh", "-c", command]
        mode = f"{eng.label} {image}{'' if network else ' (network none)'}"
    elif trusted:
        argv = command
        mode = "local-trusted (격리 없음)"
    else:
        shutil.rmtree(work, ignore_errors=True)
        raise ToolError("capability_unavailable", "Docker 데몬이 없어 격리 실행 불가",
                        recovery="oss-analyzer setup --sandbox (WSL 에 Docker Engine) 후 재시도, "
                                 "또는 신뢰하는 저장소면 trusted=True(로컬 임시 사본에서 실행)")
    env = safe_env()
    try:  # 실패해도 임시 사본은 지운다
        try:
            proc = subprocess.run(argv, cwd=copy, shell=isinstance(argv, str), capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as e:
            raise ToolError("timeout", f"실행이 {timeout}초를 넘김") from e
        out_tail = proc.stdout.decode("utf-8", "replace")[-3000:]
        err_tail = proc.stderr.decode("utf-8", "replace")[-3000:]
        report_path = copy / report
        if not report_path.exists():
            raise ToolError("engine_error", f"리포트 {report} 가 생기지 않음 (exit {proc.returncode})",
                            data={"stdout_tail": out_tail, "stderr_tail": err_tail})
        kept = work_root / f"{proc.returncode}-{report_path.name}"
        shutil.copy2(report_path, kept)
        result = ingest(conn, proj, kept, roots=[str(copy), "/work"], command=command)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"mode": mode, "exit_code": proc.returncode, "report": str(kept), "stdout_tail": out_tail[-1500:],
            "stderr_tail": err_tail[-1500:], **result}
