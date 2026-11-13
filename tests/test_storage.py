"""저장 구조 — 예전 배치 옮기기, 휴지통·되살리기·비우기, 오래된 실행 정리, 설정, 사용량."""
import json
import os
import time

import pytest

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import inbox, storage


def _done_run(settings, archived=False, accepted=True):
    run = inbox.new_run(settings, {"mode": "request", "target_root": ".", "name": "x"})
    inbox.update_run(settings, run["id"], lambda r: r.update(status="done", accepted=accepted, archived=archived))
    return run["id"]


def test_migrate_moves_loose_files_and_old_temp_copies(settings):
    home = settings.home
    home.mkdir(parents=True, exist_ok=True)
    (home / "app_prefs.json").write_text(json.dumps({"shortcut_dismissed": True}), encoding="utf-8")
    (home / "agents_cache.json").write_text("[]", encoding="utf-8")
    (home / "app.ico").write_bytes(b"ico")
    (home / "harness").mkdir()
    old = home / "runs" / "port-abc"
    old.mkdir(parents=True)
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    rid = _done_run(settings)
    (inbox.run_dir(settings, rid) / "claude-rules.md").write_text("r", encoding="utf-8")
    moved = storage.migrate(settings)
    assert storage.load_prefs(settings)["shortcut_dismissed"] is True and not (home / "app_prefs.json").exists()
    assert (home / "cache" / "agents.json").is_file() and (home / "app" / "app.ico").is_file()
    assert not (home / "harness").exists() and not old.exists()
    assert (inbox.run_dir(settings, rid) / "agent" / "claude-rules.md").is_file()
    assert inbox.load_run(settings, rid)  # 실행 폴더는 그대로
    assert storage.migrate(settings) == [] and moved  # 두 번 해도 같다


def test_trash_restore_empty_and_prune(settings):
    a, b, c = _done_run(settings), _done_run(settings, archived=True, accepted=False), _done_run(settings, accepted=False)
    out = storage.trash_run(settings, a)
    assert not inbox.run_dir(settings, a).exists() and storage.trash_list(settings)[0]["run_id"] == a
    storage.restore_run(settings, storage.trash_list(settings)[0]["name"])
    assert inbox.load_run(settings, a)["id"] == a and out["trashed"] == a
    old = time.time() - 40 * 86400
    for rid in (a, b, c):
        for f in ("run.json", "events.jsonl"):
            p = inbox.run_dir(settings, rid) / f
            if p.exists():
                os.utime(p, (old, old))
    plan = storage.prune_runs(settings, 30, dry_run=True)
    assert set(plan["runs"]) == {a, b}  # 끝나고 수락한 것·숨긴 것만, 진행 중(c)은 아님
    storage.prune_runs(settings, 30)
    assert {t["run_id"] for t in storage.trash_list(settings)} == {a, b} and inbox.load_run(settings, c)
    assert len(storage.empty_trash(settings)["removed"]) == 2 and storage.trash_list(settings) == []


def test_trash_refuses_running_run_and_bad_settings(settings):
    rid = _done_run(settings)
    lock = inbox.run_dir(settings, rid) / "runner.lock"
    lock.write_text(json.dumps({"token": "t"}), encoding="utf-8")
    with pytest.raises(ToolError):
        storage.trash_run(settings, rid)
    with pytest.raises(ToolError):
        storage.save_prefs(settings, {"cleanup_days": 0})
    with pytest.raises(ToolError):
        storage.save_prefs(settings, {"secret": 1})
    assert storage.save_prefs(settings, {"cleanup_days": 30})["cleanup_days"] == 30


def test_report_lists_areas_with_sizes(settings):
    _done_run(settings)
    rep = storage.report(settings)
    runs = next(a for a in rep["areas"] if a["name"] == "runs")
    assert runs["kind"] == "data" and runs["bytes"] > 0 and rep["runs"]["total"] == 1
    assert inbox.load_run(settings, inbox.list_runs(settings)[0]["id"])["schema"] == inbox.RUN_SCHEMA
