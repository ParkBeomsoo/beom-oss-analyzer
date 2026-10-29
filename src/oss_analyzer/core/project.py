"""프로젝트 = (분석 대상 저장소, 고정 커밋). 커밋마다 별도 프로젝트라 버전 비교가 쉽다.

작업 공간: <home>/projects/<project_id>/
  repo/       분석 전용 clone (사용자의 작업 트리와 분리 → 분석 중 코드가 바뀌지 않음)
  ledger.db   원장
  reports/    내보낸 리포트
"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from oss_analyzer.adapters import git
from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import ledger

_conns: dict[str, sqlite3.Connection] = {}
_lock = threading.Lock()


def _slug(text: str) -> str:
    text = text.rstrip("/").removesuffix(".git")
    base = re.split(r"[/\\:]", text)[-1] or "repo"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", base).strip("-").lower() or "repo"


def workspace(settings: Settings, project_id: str) -> Path:
    return settings.projects_dir / project_id


def connection(settings: Settings, project_id: str) -> sqlite3.Connection:
    with _lock:
        if project_id not in _conns:
            db = workspace(settings, project_id) / "ledger.db"
            if not db.exists():
                raise ToolError("not_found", f"프로젝트 없음: {project_id}", recovery="oss_repo_open 으로 먼저 열기")
            _conns[project_id] = ledger.connect(db)
        return _conns[project_id]


def load(settings: Settings, project_id: str) -> tuple[sqlite3.Connection, dict]:
    conn = connection(settings, project_id)
    row = ledger.row_to_dict(conn.execute("SELECT * FROM project LIMIT 1").fetchone())
    if row is None:
        raise ToolError("not_found", f"프로젝트 메타데이터 없음: {project_id}")
    return conn, row


def open_repo(settings: Settings, source: str, ref: str | None = None, name: str | None = None) -> dict:
    warnings = []
    src_path = Path(source)
    if src_path.exists():
        source = str(src_path.resolve())
        try:
            dirty = git.run(["status", "--porcelain"], cwd=src_path).decode().strip()
        except ToolError:
            dirty = ""
        if dirty:
            warnings.append({"code": "dirty_worktree", "message": "원본 작업 트리의 커밋되지 않은 변경은 분석에 포함되지 않음 (고정 커밋 기준)"})
        commit = git.resolve_commit(src_path, ref or "HEAD")
    else:
        commit = None

    slug = _slug(name or source)
    if commit:
        project_id = f"{slug}-{commit[:8]}"
        ws = workspace(settings, project_id)
        if (ws / "ledger.db").exists():
            conn, project = load(settings, project_id)
            return {"project": project, "reused": True, "_warnings": warnings}
        repo = ws / "repo"
        if not repo.exists():
            git.clone(source, repo)
    else:
        # 원격 URL — 임시 이름으로 clone 후 커밋을 확정하고 폴더 이름을 맞춘다
        tmp = settings.projects_dir / f".cloning-{slug}"
        if tmp.exists():
            raise ToolError("invalid_request", f"이전 clone 잔여물이 있음: {tmp}", recovery="폴더를 확인 후 직접 정리")
        git.clone(source, tmp)
        commit = git.resolve_commit(tmp, ref or "HEAD")
        project_id = f"{slug}-{commit[:8]}"
        ws = workspace(settings, project_id)
        if (ws / "ledger.db").exists():
            conn, project = load(settings, project_id)
            return {"project": project, "reused": True, "note": f"임시 clone {tmp} 는 정리 필요", "_warnings": warnings}
        ws.mkdir(parents=True, exist_ok=True)
        tmp.rename(ws / "repo")
        repo = ws / "repo"

    git.checkout_detached(repo, commit)
    conn = connection_create(settings, project_id)
    conn.execute(
        "INSERT INTO project (id, source, commit_sha, repo_dir, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, source, commit, str(repo), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    _, project = load(settings, project_id)
    return {"project": project, "reused": False, "_warnings": warnings}


def connection_create(settings: Settings, project_id: str) -> sqlite3.Connection:
    db = workspace(settings, project_id) / "ledger.db"
    with _lock:
        _conns[project_id] = ledger.connect(db)
        return _conns[project_id]


def list_projects(settings: Settings) -> list[dict]:
    out = []
    if not settings.projects_dir.exists():
        return out
    for ws in sorted(settings.projects_dir.iterdir()):
        if (ws / "ledger.db").exists():
            try:
                _, project = load(settings, ws.name)
                out.append({"id": project["id"], "source": project["source"], "commit_sha": project["commit_sha"],
                            "inventory_at": project["inventory_at"]})
            except ToolError:
                continue
    return out


def close(project_id: str) -> None:
    with _lock:
        conn = _conns.pop(project_id, None)
    if conn is not None:
        conn.close()
