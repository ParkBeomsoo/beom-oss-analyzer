"""기획 모드(기획서를 쓰고 끝)와 자유 대화 모드(메시지마다 한 번, 우리 절차 없이)."""
import json
import sys
import threading
import time

from oss_analyzer.core import inbox, runner

FAKE = 'import json, os, sys\nfrom pathlib import Path\nfrom oss_analyzer.config import load_settings\nfrom oss_analyzer.core import inbox\nprompt = sys.stdin.buffer.read().decode("utf-8")\nsettings = load_settings()\nrun_id = os.environ["FAKE_RUN"]\nrun = inbox.load_run(settings, run_id)\nroot = Path(os.getcwd())\nPath(os.environ["FAKE_LOG"]).open("a", encoding="utf-8").write(json.dumps({"prompt": prompt, "mode": run["mode"]}, ensure_ascii=False) + "\\n")\nprint(json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}), flush=True)\nif run["mode"] == "plan":\n    (root / ".oss" / "brief").mkdir(parents=True, exist_ok=True)\n    (root / ".oss" / "brief" / "기획서.md").write_text("# 카페 적립\\n\\n## 요구사항\\n- R1 번호로 적립\\n", encoding="utf-8")\n    inbox.update_run(settings, run_id, lambda r: r.update(handoff={"summary": "카페 적립 기획", "how_to_run": ".oss/brief/기획서.md"}))\nelse:\n    (root / f"note{run[\'round\']}.txt").write_text(prompt, encoding="utf-8")\nprint(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "했습니다"}, {"type": "tool_use", "name": "Write", "input": {}}]}}), flush=True)\nprint(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "s1", "result": "ok"}), flush=True)\n'


def _runner(tmp_path, settings, monkeypatch, mode, request):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("OSS_ANALYZER_HOME", str(settings.home))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    run = runner.create(settings, mode=mode, target_root=str(tmp_path / "work"), request=request, name=mode)
    monkeypatch.setenv("FAKE_RUN", run["id"])
    return run, runner.Runner(settings, run["id"], exe=[sys.executable, str(fake)])


def _wait(settings, run_id, status, n=600):
    for _ in range(n):
        if inbox.load_run(settings, run_id)["status"] == status:
            return True
        time.sleep(0.1)
    return False


def test_plan_mode_writes_a_spec_and_finishes(tmp_path, settings, monkeypatch):
    run, r = _runner(tmp_path, settings, monkeypatch, "plan", "동네 카페 단골 적립 앱")
    assert run["project_id"] is None and run["port_id"] is None  # 원장 없이
    out = r.run()
    assert out["status"] == "done" and (tmp_path / "work" / ".oss" / "brief" / "기획서.md").is_file()
    call = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "[기획할 것]" in call["prompt"] and "동네 카페" in call["prompt"]
    assert r._rules(inbox.load_run(settings, run["id"])) == runner.PLAN_RULES


def test_free_mode_runs_once_per_message(tmp_path, settings, monkeypatch):
    run, r = _runner(tmp_path, settings, monkeypatch, "free", "README 를 써 줘")
    t = threading.Thread(target=r.run)
    t.start()
    try:
        assert _wait(settings, run["id"], "waiting_input")  # 한 번 하고 내 차례
        assert (tmp_path / "work" / "note1.txt").read_text(encoding="utf-8") == "README 를 써 줘"  # 절차 없이 그대로
        inbox.update_run(settings, run["id"], lambda x: x.setdefault("messages", []).append({"text": "영어로도", "at": "t", "delivered": False}))
        for _ in range(600):
            if (tmp_path / "work" / "note2.txt").exists():
                break
            time.sleep(0.1)
        assert (tmp_path / "work" / "note2.txt").read_text(encoding="utf-8") == "영어로도"
        assert _wait(settings, run["id"], "waiting_input")
    finally:
        r.stop()
        t.join(timeout=30)
    assert r._rules(inbox.load_run(settings, run["id"])) == runner.FREE_RULES
