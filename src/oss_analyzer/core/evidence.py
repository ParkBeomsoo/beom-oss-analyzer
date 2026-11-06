"""근거(Evidence) — 결론을 뒷받침하는 위치·도구·등급 레코드 (소스 분석용).

- id 는 의미 있는 내용의 해시(`ev_` + sha256) → 같은 근거는 자동으로 중복 제거된다.
- 위치는 file-line-range + 고정 커밋 + 스니펫 해시. 기록 시점에 실제 파일과 대조해 검증한다.
- 비밀 파일(secret)은 근거로 쓸 수 없다 (내용이 결과·모델로 새어 나가는 것을 막기 위해).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oss_analyzer.adapters import git
from oss_analyzer.common.response import ToolError
from oss_analyzer.core import ledger
from oss_analyzer.core.levels import PROVIDER_LEVEL

CONFIDENCE = {"observed", "derived", "inferred", "experimental"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_snippet(lines: list[str]) -> str:
    return "\n".join(line.rstrip() for line in lines)


def snippet_hash(lines: list[str]) -> str:
    return _sha(normalize_snippet(lines))[:16]


_LINES_CACHE: dict[tuple[str, str, str], list[str]] = {}


def decode(raw: bytes) -> str:
    """UTF-8 우선, 실패하면 한국어 레거시 인코딩(CP949) 시도."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp949")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def read_lines(project: dict, path: str) -> list[str]:
    """고정 커밋의 파일 줄 목록 (프로세스 안에서 캐시).

    분석 전용 clone 은 고정 커밋에 체크아웃돼 있고 우리가 수정하지 않으므로 작업 트리에서 읽는다.
    (파일마다 git show 를 부르면 수백 개 근거 검증에 수십 초가 걸려 DB 쓰기 잠금이 길어졌다)
    """
    key = (project["repo_dir"], project["commit_sha"], path)
    if key not in _LINES_CACHE:
        file = Path(project["repo_dir"]) / path
        raw = file.read_bytes() if file.is_file() else git.show(Path(project["repo_dir"]), project["commit_sha"], path)
        _LINES_CACHE[key] = decode(raw).splitlines()
    return _LINES_CACHE[key]


def validate_location(conn, project: dict, loc: dict) -> dict:
    """위치를 검증하고 정규화된 위치(커밋·스니펫 해시 포함)를 돌려준다. 실패 시 evidence_rejected."""
    path = str(loc.get("path", "")).replace("\\", "/").removeprefix("./")
    try:
        start = int(loc["start_line"])
        end = int(loc.get("end_line", start))
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError("evidence_rejected", f"start_line/end_line 이 필요함: {loc}") from e
    row = conn.execute("SELECT category FROM file WHERE path = ?", (path,)).fetchone()
    if row is None:
        raise ToolError("evidence_rejected", f"고정 커밋에 없는 파일: {path}", recovery="oss_repo_status 의 commit 기준 경로인지 확인")
    if row["category"] == "secret":
        raise ToolError("evidence_rejected", f"비밀 파일은 근거로 쓸 수 없음: {path}")
    lines = read_lines(project, path)
    if start < 1 or end < start or end > len(lines):
        raise ToolError("evidence_rejected", f"{path} 줄 범위 {start}-{end} 가 파일 길이 {len(lines)} 를 벗어남")
    digest = snippet_hash(lines[start - 1:end])
    if loc.get("snippet_hash") and loc["snippet_hash"] != digest:
        raise ToolError("evidence_rejected", f"{path}:{start}-{end} 스니펫 해시 불일치 — 코드가 다르거나 줄 번호가 틀림")
    return {
        "kind": "file-line-range", "path": path, "start_line": start, "end_line": end,
        "commit": project["commit_sha"], "snippet_hash": digest,
    }


def record(
    conn, project: dict, *, provider: str, operation: str, parameters: dict, result: Any,
    locations: list[dict], confidence: str, limitations: list[str] | None = None,
    level: int | None = None, validated: bool = False,
) -> dict:
    if provider not in PROVIDER_LEVEL:
        raise ToolError("invalid_request", f"알 수 없는 provider: {provider}")
    if confidence not in CONFIDENCE:
        raise ToolError("invalid_request", f"confidence 는 {sorted(CONFIDENCE)} 중 하나")
    max_level = PROVIDER_LEVEL[provider]
    level = max_level if level is None else min(level, max_level)
    locs = locations if validated else [validate_location(conn, project, loc) for loc in locations]
    semantic = {
        "provider": provider, "operation": operation, "parameters": parameters,
        "result": result, "locations": locs, "confidence": confidence, "level": level,
    }
    ev_id = "ev_" + _sha(ledger.dumps(semantic))[:24]
    conn.execute(
        "INSERT OR IGNORE INTO evidence (id, provider, operation, parameters, result, locations, confidence, level, limitations, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ev_id, provider, operation, ledger.dumps(parameters), ledger.dumps(result), ledger.dumps(locs),
            confidence, level, ledger.dumps(limitations or []), datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    return {"evidence_id": ev_id, "level": level, "locations": locs}


def get(conn, ev_id: str) -> dict:
    row = ledger.row_to_dict(conn.execute("SELECT * FROM evidence WHERE id = ?", (ev_id,)).fetchone())
    if row is None:
        raise ToolError("not_found", f"근거 없음: {ev_id}")
    return row


def revalidate(conn, project: dict, ev: dict) -> list[str]:
    """근거 위치의 스니펫이 아직 같은지 재검사 (증분 재분석용). 바뀐 위치 목록 반환."""
    stale = []
    for loc in ev["locations"]:
        try:
            validate_location(conn, project, loc)
        except ToolError:
            stale.append(f"{loc['path']}:{loc['start_line']}-{loc['end_line']}")
    return stale
