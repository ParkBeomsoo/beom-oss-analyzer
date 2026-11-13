"""데이터 저장 구조 — 무엇을 어디에 두고, 언제 지우는가.

Claude Code(~/.claude)·Codex(~/.codex)처럼 홈 아래 폴더 하나에 모은다. 안은 성격별로 나눈다:

  settings.json        사용자 설정 (앱 기본값·정리 주기) — 지우면 기본값으로
  projects/<id>/       분석·포팅 원장(ledger.db, 해시 사슬)·원본 클론·골든 — 핵심 기록, 지우면 되돌릴 수 없음
  runs/<run>/          자동 실행 하나 = 대화 하나: run.json(상태) · events.jsonl(흐름, 추가만) · inbox/(질문·승인 요청)
                       · shadow.git(체크포인트) · agent/(라운드마다 다시 만드는 에이전트 설정)
  trash/<run>-<시각>/  지운 실행 — 정해진 날이 지나면 비움 (기본 7일)
  app/                 앱 아이콘 (바로가기가 가리킴)
  cache/               다시 만들 수 있는 것 (에이전트 상태, 검증 하네스) — 지워도 됨
  work/                검증·커버리지용 임시 사본 — 쓰고 바로 지움
  bin/ engines/ node/  setup 이 설치한 분석 엔진 — 지우면 setup 으로 다시 설치
  cbm-cache/           구조 지도 엔진의 색인 캐시 — 지워도 다시 만듦
  locks/               프로세스 잠금

비밀은 저장하지 않는다 (키·토큰은 환경 변수로만 쓰고, 출력에서는 가린다). 모든 것은 이 컴퓨터에만 남는다.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings

SCHEMA = 1  # settings.json
DEFAULTS = {"schema": SCHEMA, "trash_days": 7, "cleanup_days": None, "shortcut_dismissed": False}
AREAS = [  # (이름, 폴더, 성격, 지워도 되는지)
    ("projects", "projects", "data", False),
    ("runs", "runs", "data", False),
    ("trash", "trash", "trash", True),
    ("cache", "cache", "cache", True),
    ("app", "app", "app", False),
    ("cbm-cache", "cbm-cache", "cache", True),
    ("work", "work", "temp", True),
    ("engines", "engines", "tools", True),
    ("bin", "bin", "tools", True),
    ("node", "node", "tools", True),
    ("backups", "backups", "data", False),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cache_dir(settings: Settings) -> Path:
    d = settings.home / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir(settings: Settings) -> Path:
    d = settings.home / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def trash_dir(settings: Settings) -> Path:
    d = settings.home / "trash"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 설정 (settings.json)

def settings_file(settings: Settings) -> Path:
    return settings.home / "settings.json"


def load_prefs(settings: Settings) -> dict:
    try:
        data = json.loads(settings_file(settings).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {**DEFAULTS, **data}
    except (OSError, ValueError):
        pass
    return dict(DEFAULTS)


def save_prefs(settings: Settings, patch: dict) -> dict:
    allowed = {"trash_days", "cleanup_days", "shortcut_dismissed", "defaults"}  # defaults: 새 작업에 쓸 지난 선택
    bad = set(patch) - allowed
    if bad:
        raise ToolError("invalid_request", f"모르는 설정: {sorted(bad)}")
    for k in ("trash_days", "cleanup_days"):
        if k in patch and patch[k] is not None and (not isinstance(patch[k], int) or not 1 <= patch[k] <= 3650):
            raise ToolError("invalid_request", f"{k} 는 1~3650 일 또는 null(끔)")
    prefs = {**load_prefs(settings), **patch, "schema": SCHEMA}
    settings.home.mkdir(parents=True, exist_ok=True)
    tmp = settings_file(settings).with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, settings_file(settings))
    return prefs


# ---------------------------------------------------------------- 예전 배치에서 옮기기

def migrate(settings: Settings) -> list[str]:
    """예전 버전이 홈 바로 아래 흩어 둔 파일을 제자리로. 몇 번 실행해도 같다."""
    home, moved = settings.home, []
    if not home.is_dir():
        return moved
    old_prefs = home / "app_prefs.json"
    if old_prefs.is_file():
        try:
            data = json.loads(old_prefs.read_text(encoding="utf-8"))
            save_prefs(settings, {k: v for k, v in data.items() if k in ("shortcut_dismissed",)})
        except (OSError, ValueError, ToolError):
            pass
        old_prefs.unlink(missing_ok=True)
        moved.append("app_prefs.json → settings.json")
    for name, new in (("agents_cache.json", "cache/agents.json"), ("app.ico", "app/app.ico"), ("app.png", "app/app.png"),
                      ("cache/app.ico", "app/app.ico"), ("cache/app.png", "app/app.png")):
        src = home / name
        if src.is_file():
            dst = home / new
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.move(str(src), dst)
            else:
                src.unlink()
            moved.append(f"{name} → {new}")
    if (home / "harness").is_dir():
        shutil.rmtree(home / "harness", ignore_errors=True)  # 매번 다시 쓰는 하네스 — 이제 cache/harness
        moved.append("harness → cache/harness")
    runs = home / "runs"
    if runs.is_dir():
        for d in runs.iterdir():
            # 실행 폴더가 아닌 것(예전 검증·커버리지 임시 사본) — 이제 work/
            if d.is_dir() and not (d / "run.json").exists() and time.time() - d.stat().st_mtime > 3600:
                shutil.rmtree(d, ignore_errors=True)
                moved.append(f"runs/{d.name} (임시 사본) 정리")
            elif (d / "run.json").exists():
                agent = d / "agent"
                for f in ("claude-mcp.json", "claude-rules.md", "gemini-settings.json"):
                    if (d / f).is_file():
                        agent.mkdir(exist_ok=True)
                        shutil.move(str(d / f), agent / f)
    return moved


# ---------------------------------------------------------------- 지우기 · 휴지통 · 정리

def _last_activity(run_dir: Path) -> float:
    ts = [p.stat().st_mtime for p in (run_dir / "run.json", run_dir / "events.jsonl") if p.exists()]
    return max(ts) if ts else run_dir.stat().st_mtime


def trash_run(settings: Settings, run_id: str) -> dict:
    """실행을 휴지통으로 — 대화 흐름·체크포인트가 함께 옮겨지고, 원장(승인·검증 기록)은 프로젝트에 남는다."""
    from oss_analyzer.core import inbox, runner

    d = inbox.run_dir(settings, run_id)
    if not d.is_dir():
        raise ToolError("not_found", f"실행 없음: {run_id}")
    if runner.lock_owner(settings, run_id) is not None:
        raise ToolError("invalid_request", "진행 중인 실행은 지울 수 없습니다. 먼저 멈춰 주세요")
    dst = trash_dir(settings) / f"{run_id}-{time.strftime('%Y%m%d%H%M%S')}"
    shutil.move(str(d), dst)
    (dst / ".trashed").write_text(_now(), encoding="utf-8")
    return {"trashed": run_id, "to": str(dst)}


def restore_run(settings: Settings, name: str) -> dict:
    import re

    if not re.fullmatch(r"run_[0-9a-f]{12}-\d{14}", name or ""):
        raise ToolError("invalid_request", f"휴지통 항목 이름이 아님: {name}")
    src = trash_dir(settings) / name
    run_id = name.split("-")[0]
    if not src.is_dir() or src.resolve().parent != trash_dir(settings).resolve():
        raise ToolError("not_found", f"휴지통에 없음: {name}")
    from oss_analyzer.core import inbox

    dst = inbox.run_dir(settings, run_id)
    if dst.exists():
        raise ToolError("conflict", "같은 실행이 이미 있습니다")
    (src / ".trashed").unlink(missing_ok=True)
    shutil.move(str(src), dst)
    return {"restored": run_id}


def trash_list(settings: Settings) -> list[dict]:
    out = []
    for d in sorted(trash_dir(settings).iterdir(), reverse=True):
        if not d.is_dir():
            continue
        try:
            run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            run = {}
        try:
            at = (d / ".trashed").read_text(encoding="utf-8").strip()
        except OSError:
            at = None
        out.append({"name": d.name, "run_id": run.get("id"), "title": run.get("name"), "trashed_at": at})
    return out


def empty_trash(settings: Settings, older_than_days: int | None = None) -> dict:
    gone = []
    limit = time.time() - older_than_days * 86400 if older_than_days else None
    for d in trash_dir(settings).iterdir():
        if not d.is_dir():
            continue
        stamp = d / ".trashed"
        when = stamp.stat().st_mtime if stamp.exists() else d.stat().st_mtime
        if limit is None or when < limit:
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d.name)
    return {"removed": gone}


def prune_runs(settings: Settings, days: int, dry_run: bool = False) -> dict:
    """끝났고(수락) 또는 숨긴 실행 중 days 일 동안 아무 일도 없던 것을 휴지통으로. 진행 중·확인 대기는 건드리지 않는다."""
    from oss_analyzer.core import inbox, runner

    limit = time.time() - days * 86400
    picked = []
    for r in inbox.list_runs(settings):
        d = inbox.run_dir(settings, r["id"])
        idle = r.get("status") not in ("running", "waiting_approval", "waiting_plan", "waiting_answer", "waiting_input", "preparing")
        finished = (r.get("status") == "done" and r.get("accepted")) or (r.get("archived") and idle)
        if finished and _last_activity(d) < limit and runner.lock_owner(settings, r["id"]) is None:
            picked.append(r["id"])
    if not dry_run:
        for rid in picked:
            trash_run(settings, rid)
    return {"runs": picked, "dry_run": dry_run}


def maintenance(settings: Settings) -> dict:
    """앱·CLI 시작 때 한 번 — 예전 배치 옮기기, 오래된 휴지통 비우기, 설정한 경우 오래된 실행 정리."""
    out = {"migrated": migrate(settings)}
    prefs = load_prefs(settings)
    if prefs.get("trash_days"):
        out["trash"] = empty_trash(settings, prefs["trash_days"])
    if prefs.get("cleanup_days"):
        out["pruned"] = prune_runs(settings, prefs["cleanup_days"])
    return out


# ---------------------------------------------------------------- 사용량

def _size(p: Path, budget: list[int]) -> int:
    total = 0
    stack = [p]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    budget[0] -= 1
                    if budget[0] < 0:
                        return -total  # 너무 많으면 여기까지 (음수 = 최소값)
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def report(settings: Settings, sizes: bool = True) -> dict:
    """어디에 무엇이 얼마나 — 영역별 크기(파일이 아주 많으면 '이상'), 실행 수, 휴지통, 설정."""
    from oss_analyzer.core import inbox

    areas = []
    for name, folder, kind, removable in AREAS:
        p = settings.home / folder
        if not p.exists():
            continue
        row = {"name": name, "path": str(p), "kind": kind, "removable": removable}
        if sizes:
            n = _size(p, [400_000])
            row.update(bytes=abs(n), at_least=n < 0)
        areas.append(row)
    runs = inbox.list_runs(settings)
    return {"root": str(settings.home), "areas": areas, "settings": load_prefs(settings),
            "runs": {"total": len(runs), "archived": sum(1 for r in runs if r.get("archived")),
                     "finished": sum(1 for r in runs if r.get("status") == "done" and r.get("accepted"))},
            "trash": trash_list(settings)}
