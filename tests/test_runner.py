"""자동 실행기 — 가짜 claude 로 라운드 진행·승인 대기·완료 판정·환경 처리·권한 규칙을 확인 (실제 Claude Code 없이)."""
import json

import pytest
import os
import sys
import threading
import time
from pathlib import Path

from oss_analyzer.core import inbox, port, project, runner
from oss_analyzer.core import port_guard as guard

FAKE = r'''import json, os, re, sys
from pathlib import Path
from oss_analyzer.config import load_settings
from oss_analyzer.core import make, port, project
argv = sys.argv[1:]
prompt = sys.stdin.buffer.read().decode("utf-8")
rules = Path(argv[argv.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
mcp = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8").write(json.dumps({"argv": argv, "has_key": "ANTHROPIC_API_KEY" in os.environ, "multiline": "\n" in prompt, "rules": "oss_handoff" in rules, "mcp": list(mcp["mcpServers"])}) + "\n")
print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1", "mcp_servers": [{"name": "oss-analyzer", "status": "connected"}]}), flush=True)
m = re.search(r"project_id=(\S+) port_id=(\S+)", prompt)
settings = load_settings()
conn, proj = project.load(settings, m.group(1))
pid = m.group(2)
root = Path(os.getcwd())
from oss_analyzer.core import inbox
run_id = os.environ["FAKE_RUN"]
if "--resume" not in argv:
    make.set_requirements(conn, pid, [{"text": "수량 x 2000 총액"}])
    (root / ".oss").mkdir(exist_ok=True)
    (root / ".oss" / "cases.json").write_text(json.dumps({"cases": [{"name": "one", "argv": ["3"], "req": "R1", "expect": {"equals": {"total": 6000}}}]}), encoding="utf-8")
    port.capture(conn, proj, settings, pid, str(root / ".oss" / "cases.json"))
    text = "요구사항과 합격 기준을 만들었습니다. 승인을 기다립니다."
elif "oss_plan_set" in prompt:
    r = inbox.load_run(settings, run_id); r["plan"] = {"steps": [{"title": "app.py 작성", "files": ["app.py"], "status": "todo"}], "approved": False}; inbox.save_run(settings, r)
    text = "계획을 냈습니다."
elif "oss_handoff" in prompt:
    r = inbox.load_run(settings, run_id); r["handoff"] = {"summary": "app.py 추가", "how_to_run": "python app.py 3"}; inbox.save_run(settings, r)
    text = "인수인계를 남겼습니다."
else:
    (root / "app.py").write_text("import json, sys\nq = int(sys.argv[1])\nprint(json.dumps({'total': q * 2000}))\n", encoding="utf-8")
    port.map_items(conn, pid, [{"item": "R1", "status": "ported", "target": "app.py:3"}])
    py = f'"{sys.executable}"'
    port.verify(conn, proj, settings, pid, target_cli=f"{py} -m coverage run --append --data-file=.cov app.py",
                target_coverage_cmd=f"{py} -m coverage json --data-file=.cov -o cov.json", target_coverage_report="cov.json")
    text = "구현하고 검증했습니다."
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}, {"type": "tool_use", "name": "mcp__oss-analyzer__oss_port_status", "input": {}}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-1", "result": text, "total_cost_usd": 0.01, "num_turns": 3}), flush=True)
'''


def test_runner_rounds_wait_for_approval_and_finish(tmp_path, settings, monkeypatch):
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("OSS_ANALYZER_HOME", str(settings.home))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
    run = runner.create(settings, mode="request", target_root=str(tmp_path / "mine"), request="수량을 받아 총액을 낸다",
                        name="총액", max_rounds=4, confirm_at="start")
    monkeypatch.setenv("FAKE_RUN", run["id"])
    r = runner.Runner(settings, run["id"], exe=[sys.executable, str(fake)])
    out = {}
    t = threading.Thread(target=lambda: out.update(r.run()))
    t.start()
    for _ in range(900):  # 가짜 에이전트도 매 라운드 패키지를 새로 import — 첫 실행은 느리다
        if inbox.load_run(settings, run["id"])["status"] == "waiting_approval":
            break
        time.sleep(0.2)
    got = inbox.load_run(settings, run["id"])
    assert got["status"] == "waiting_approval" and (got.get("plan") or {}).get("steps")  # 처음에 한 번: 기준과 계획이 함께
    conn, _ = project.load(settings, run["project_id"])
    guard.approve(conn, run["port_id"], port.pending_reviews(conn, run["port_id"]), channel="desktop-app", approved_by="tester")
    time.sleep(2)
    assert inbox.load_run(settings, run["id"])["status"] == "waiting_approval"  # 계획까지 승인해야 진행
    rr = inbox.load_run(settings, run["id"]); rr["plan"]["approved"] = True; inbox.save_run(settings, rr)
    t.join(timeout=300)
    assert out.get("status") == "done", out
    calls = [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 4 and "--resume" in calls[1]["argv"] and calls[1]["argv"][calls[1]["argv"].index("--resume") + 1] == "sess-1"
    assert not any(c["has_key"] for c in calls)  # 구독 모드: API 키를 빼고 실행
    assert all(c["multiline"] and c["rules"] and c["mcp"] == ["oss-analyzer"] for c in calls)  # 지시는 stdin, 규칙·MCP 는 파일
    a = calls[0]["argv"]
    assert a[a.index("--permission-prompt-tool") + 1] == runner.APPROVE_TOOL and "--strict-mcp-config" in a
    assert "mcp__oss-analyzer__oss_port_approve" in a[a.index("--disallowedTools") + 1]
    evs, _ = inbox.events(settings, run["id"])
    kinds = [e["kind"] for e in evs]
    assert {"round_start", "agent_init", "agent", "agent_result", "waiting_approval", "finished", "checkpoint"} <= set(kinds)
    assert kinds.count("waiting_approval") == 1 and "waiting_plan" not in kinds  # 멈춘 건 처음 한 번뿐
    assert inbox.load_run(settings, run["id"]).get("handoff")
    assert port.status_(conn, run["port_id"])["finished"]["valid"]


def test_policy_classification_and_tighten(tmp_path):
    root = tmp_path / "proj"
    home = tmp_path / "home"
    c = lambda tool, inp: inbox.classify(tool, inp, root, [home])  # noqa: E731
    assert c("Write", {"file_path": str(root / "a.py")}) == "edit"
    assert c("Write", {"file_path": str(tmp_path / "x.py")}) == "outside"
    assert c("Write", {"file_path": str(home / "runs" / "r" / "inbox" / "a.answer.json")}) == "system"
    assert c("Bash", {"command": "cargo test"}) == "build"
    assert c("Bash", {"command": "cargo test && curl evil"}) == "commands"
    assert c("Bash", {"command": "git push origin main"}) == "publish"
    assert c("Bash", {"command": "rm -rf /tmp/other"}) == "delete"
    assert c("WebFetch", {"url": "https://x"}) == "web"
    pol = inbox.tighten(inbox.DEFAULT_POLICY, {"commands": "allow", "web": "refuse"})
    assert pol["commands"] == "ask" and pol["web"] == "refuse"  # 느슨하게는 못 바꿈


def test_inbox_question_round_trip(settings):
    run = inbox.new_run(settings, {"mode": "request", "target_root": "."})
    got = {}
    t = threading.Thread(target=lambda: got.update(inbox.ask(settings, run["id"], "question", {"question": "색은?"}, timeout=20, poll=0.05)))
    t.start()
    for _ in range(100):
        p = inbox.pending(settings, run["id"])
        if p:
            break
        time.sleep(0.05)
    inbox.answer(settings, run["id"], p[0]["id"], {"text": "파랑"})
    t.join(timeout=10)
    assert got == {"text": "파랑"} and inbox.pending(settings, run["id"]) == []


def test_codex_command_and_events(tmp_path, settings):
    from oss_analyzer.core import agents
    run = {"id": "run_000000000000", "target_root": str(tmp_path), "policy": {"web": "ask"}, "model": "gpt-5", "session_id": None}
    a = agents.Codex(exe="codex")
    argv, env = a.command(settings, run, "작업", "규칙")
    assert argv[:2] == ["codex", "exec"] and "--json" in argv and "-a" not in argv and "-s" not in argv
    assert argv[-1] == "-" and a.stdin_text(run, "작업", "규칙") == "규칙\n\n작업"
    cfg = [argv[i + 1] for i, x in enumerate(argv) if x == "-c"]
    assert any(c.startswith("mcp_servers.oss-analyzer.command=") for c in cfg)
    assert "sandbox_workspace_write.network_access=false" in cfg and 'sandbox_mode="workspace-write"' in cfg
    assert 'mcp_servers.oss-analyzer.default_tools_approval_mode="approve"' in cfg  # "auto" 는 never 정책에서 전부 거절됨
    assert any(c.startswith("mcp_servers.oss-analyzer.startup_timeout_sec=") for c in cfg)
    assert 'mcp_servers.oss-analyzer.startup_readiness="catalog"' in cfg and "mcp_servers.oss-analyzer.required=true" in cfg
    assert (sys.platform != "win32") or 'windows.sandbox="unelevated"' in cfg
    failed = a.parse({"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": "oss_activity", "status": "failed",
                                                         "error": {"message": "requires approval"}}})
    assert failed[0]["kind"] == "tool" and "requires approval" in failed[1]["text"]
    run["session_id"] = "th_1"
    argv2, _ = a.command(settings, run, "다음", "규칙")
    assert argv2[:3] == ["codex", "exec", "resume"] and argv2[-2:] == ["th_1", "-"] and a.stdin_text(run, "다음", "규칙") == "다음"
    assert a.parse({"type": "thread.started", "thread_id": "th_1"}) == [{"kind": "init", "session_id": "th_1"}]
    assert a.parse({"type": "item.completed", "item": {"type": "agent_message", "text": "끝"}})[0]["text"] == "끝"
    assert a.parse({"type": "turn.failed", "error": {"message": "x"}})[0]["ok"] is False


def test_orphaned_question_waits_for_answer_and_rides_next_round(settings):
    """에이전트가 답을 기다리지 않고 턴을 닫아도, 다음 라운드는 사용자 답을 받은 뒤 그 답을 싣고 시작한다."""
    run = inbox.new_run(settings, {"mode": "request", "target_root": ".", "max_rounds": 3})
    d = inbox.run_dir(settings, run["id"]) / "inbox"
    (d / "aaaaaaaaaaaa.json").write_text(json.dumps({"id": "aaaaaaaaaaaa", "kind": "question", "question": "소수 허용?"}), encoding="utf-8")
    (d / "bbbbbbbbbbbb.json").write_text(json.dumps({"id": "bbbbbbbbbbbb", "kind": "permission", "summary": "x"}), encoding="utf-8")
    r = runner.Runner(settings, run["id"], exe=[sys.executable, "-c", "pass"])
    out = {}
    t = threading.Thread(target=lambda: out.update(ok=r._settle_orphans()))
    t.start()
    for _ in range(100):
        if inbox.load_run(settings, run["id"])["status"] == "waiting_answer":
            break
        time.sleep(0.05)
    assert inbox.load_run(settings, run["id"])["status"] == "waiting_answer"
    inbox.answer(settings, run["id"], "aaaaaaaaaaaa", {"text": "정수만"})
    time.sleep(1.5)
    assert inbox.load_run(settings, run["id"])["status"] == "waiting_answer"  # 권한 요청도 답을 기다린다
    inbox.answer(settings, run["id"], "bbbbbbbbbbbb", {"allow": True})
    t.join(timeout=10)
    got = inbox.load_run(settings, run["id"])
    assert out == {"ok": True} and got["status"] == "running" and inbox.pending(settings, run["id"]) == []
    texts = " ".join(m["text"] for m in got["messages"])
    assert "소수 허용?" in texts and "정수만" in texts and "허용" in got["messages"][-1]["text"]
    assert "정수만" in runner._brief(got, None)


def test_run_lock_blocks_second_runner_and_stale_lock_is_taken_over(settings):
    """한 실행은 한 곳에서만 진행된다 (앱 창·CLI·다른 창). 심장 박동이 끊긴 잠금은 넘겨받는다."""
    from oss_analyzer.common.response import ToolError
    run = inbox.new_run(settings, {"mode": "request", "target_root": ".", "max_rounds": 1})
    a = runner.Runner(settings, run["id"], exe=[sys.executable, "-c", "pass"])
    b = runner.Runner(settings, run["id"], exe=[sys.executable, "-c", "pass"])
    a._acquire()
    try:
        assert runner.lock_owner(settings, run["id"]) is not None
        try:
            b._acquire()
            raise AssertionError("두 번째 실행기가 잠금을 잡으면 안 된다")
        except ToolError as e:
            assert e.code == "conflict"
    finally:
        a._release()
    assert runner.lock_owner(settings, run["id"]) is None
    lock = inbox.run_dir(settings, run["id"]) / runner.LOCK_FILE
    lock.write_text(json.dumps({"token": "dead"}), encoding="utf-8")
    old = time.time() - runner.LOCK_STALE_SEC - 5
    os.utime(lock, (old, old))
    assert runner.lock_owner(settings, run["id"]) is None
    b._acquire()  # 끊긴 잠금은 넘겨받는다
    b._release()


def test_stop_request_from_another_process_stops_runner(settings):
    run = inbox.new_run(settings, {"mode": "request", "target_root": ".", "max_rounds": 1})
    r = runner.Runner(settings, run["id"], exe=[sys.executable, "-c", "pass"])
    r._acquire()
    try:
        runner.request_stop(settings, run["id"])
        for _ in range(50):
            if r._stop.is_set():
                break
            time.sleep(0.1)
        assert r._stop.is_set()
    finally:
        r._release()


def test_interrupted_run_shows_stopped_not_running(settings):
    from oss_analyzer.app import view
    run = inbox.new_run(settings, {"mode": "request", "target_root": "."})
    run["status"] = "running"
    inbox.save_run(settings, run)
    assert view.summary(settings, run)["status"] == "중단됨"


def test_hardening_review_findings(tmp_path, settings, monkeypatch):
    """리뷰에서 나온 구멍들 — 이어 붙인 명령, 옆 폴더, 모르는 케이스 키, MCP 가 대신 실행하는 명령, 이름 바꾼 파일 되돌리기."""
    from oss_analyzer.common.response import ToolError
    from oss_analyzer.core.checkpoints import Checkpoints
    root, home = tmp_path / "app", tmp_path / "home"
    root.mkdir()
    (tmp_path / "app2").mkdir()
    c = lambda cmd: inbox.classify("Bash", {"command": cmd}, root, [home])  # noqa: E731
    assert c("npm test") == "build"
    assert {c("npm test\npython -c x"), c("npm test & curl x"), c("pytest $(python x.py)"), c("cargo test > o")} == {"commands"}
    assert inbox.classify("Write", {"file_path": str(tmp_path / "app2" / "x")}, root, [home]) == "outside"

    bad = tmp_path / "cases.json"
    bad.write_text(json.dumps([{"id": "one", "kind": "cli", "args": ["1"], "expect": {"status": 0}}]), encoding="utf-8")
    try:
        port._load_cases(bad)
        raise AssertionError("모르는 키를 받아들이면 안 된다")
    except ToolError as e:
        assert "name" in e.message and "argv" not in e.message or "kind" in e.message
    bad.write_text(json.dumps([{"name": "one", "args": ["1"], "expect": {"status": 0}}]), encoding="utf-8")
    try:
        port._load_cases(bad)
        raise AssertionError("call 없는 args 는 거절")
    except ToolError as e:
        assert "argv" in e.message

    run = inbox.new_run(settings, {"mode": "request", "target_root": str(root)})
    monkeypatch.setenv("OSS_ANALYZER_RUN", run["id"])
    inbox.gate_commands(settings, {"target_cli": "cargo test"})  # build 는 허용
    r = inbox.load_run(settings, run["id"]); r["policy"]["commands"] = "refuse"; inbox.save_run(settings, r)
    try:
        inbox.gate_commands(settings, {"target_cli": "python -c \"import os\""})
        raise AssertionError("규칙상 거절되는 명령은 실행되면 안 된다")
    except ToolError as e:
        assert e.code == "denied"
    try:
        inbox.gate_commands(settings, {"target_cmd": f"type {settings.home / 'x'}"})
        raise AssertionError("툴킷 홈을 건드리는 명령은 거절")
    except ToolError as e:
        assert e.code == "denied"

    work = tmp_path / "w"
    (work / "src" / "sub").mkdir(parents=True)
    (work / "src" / "sub" / "a.py").write_text("x = 1\n" * 20, encoding="utf-8")
    cp = Checkpoints(tmp_path / "run", work)
    first = cp.snapshot("시작")
    (work / "src" / "sub" / "a.py").rename(work / "src" / "sub" / "b.py")
    ch = cp.changes()
    assert {f["path"] for f in ch["files"]} == {"src/sub/a.py", "src/sub/b.py"}
    cp.restore(first)
    assert (work / "src" / "sub" / "a.py").exists() and not (work / "src" / "sub" / "b.py").exists()


def test_round_survives_agent_flooding_stderr(tmp_path, settings):
    """에이전트가 stderr 로 많이 써도 라운드가 멈추지 않는다 (파이프 교착)."""
    flood = tmp_path / "flood.py"
    flood.write_text("import sys, json\nsys.stderr.write('x' * 400000)\nsys.stderr.flush()\n"
                     "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'ok'}))\n",
                     encoding="utf-8")
    run = inbox.new_run(settings, {"mode": "request", "target_root": str(tmp_path), "agent": "claude"})
    r = runner.Runner(settings, run["id"], exe=[sys.executable, str(flood)])
    out = {}
    t = threading.Thread(target=lambda: out.update(r._turn(inbox.load_run(settings, run["id"]), "작업")))
    t.start()
    t.join(timeout=60)
    assert not t.is_alive() and out.get("is_error") is False



def test_second_review_findings(tmp_path, settings, monkeypatch):
    """케이스 안의 명령도 거르고, 막 만든 빈 잠금은 주인이 있는 것으로, 자동 실행 중엔 승인 도구를 거절한다."""
    from oss_analyzer.common.response import ToolError
    cases = [{"name": "c", "call": {"target": {"cmd": "python evil.py"}}, "expect": {"status": "ok"}},
             {"name": "r", "argv": ["1"], "runtime": {"target": "python3 -X dev"}, "expect": {"status": 0}}]
    cmds = port.case_commands(cases)
    assert "python evil.py" in cmds.values() and "python3 -X dev" in cmds.values()
    root = tmp_path / "app"
    root.mkdir()
    run = inbox.new_run(settings, {"mode": "request", "target_root": str(root)})
    monkeypatch.setenv("OSS_ANALYZER_RUN", run["id"])
    r = inbox.load_run(settings, run["id"]); r["policy"]["commands"] = "refuse"; inbox.save_run(settings, r)
    try:
        inbox.gate_commands(settings, cmds)
        raise AssertionError("케이스 안의 명령도 규칙을 거쳐야 한다")
    except ToolError as e:
        assert e.code == "denied"
    assert inbox.classify("Bash", {"command": "type C:/Users/x/.oss-analyzer/runs/a.json"}, root, [settings.home]) == "system"
    inbox.gate_commands(settings, {"target_cli": "cargo test"})
    assert "cargo test" not in (inbox.load_run(settings, run["id"]).get("approved_cmds") or [])  # 규칙 허용분은 기억하지 않는다

    lock = inbox.run_dir(settings, run["id"]) / runner.LOCK_FILE
    lock.write_text("", encoding="utf-8")
    assert runner.lock_owner(settings, run["id"]) == {}  # 막 만들어진 빈 잠금도 주인이 있다

    import asyncio
    from oss_analyzer.modules import port as port_tools
    out = asyncio.run(port_tools.oss_port_approve("p", "q", None))
    assert not out["ok"] and out["errors"][0]["code"] == "denied"


def test_effort_reaches_agent_command(tmp_path, settings):
    """노력(생각 깊이) 설정이 에이전트 실행 명령으로 들어간다."""
    from oss_analyzer.core import agents
    run = {"id": "run_000000000000", "target_root": str(tmp_path), "policy": {}, "model": "gpt-6-sol", "session_id": None, "effort": "high"}
    argv, _ = agents.Codex(exe="codex").command(settings, run, "작업", "규칙")
    assert 'model_reasoning_effort="high"' in argv
    inbox.new_run(settings, {"mode": "request", "target_root": str(tmp_path)})
    run2 = {**run, "id": inbox.list_runs(settings)[0]["id"], "model": "opus"}
    argv2, _ = agents.ClaudeCode(exe="claude").command(settings, run2, "작업", "규칙")
    assert argv2[argv2.index("--effort") + 1] == "high" and argv2[argv2.index("--model") + 1] == "opus"
    assert [l["id"] for l in agents.ClaudeCode(exe="claude").efforts()[""]] == ["low", "medium", "high", "xhigh", "max"]



def test_confirm_at_end_runs_through_and_asks_once(tmp_path, settings, monkeypatch):
    """'마지막에 한 번 확인': 기준을 만든 뒤 멈추지 않고 구현·검증까지 하고, 승인만 남았을 때 한 번 멈춘다."""
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("OSS_ANALYZER_HOME", str(settings.home))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    run = runner.create(settings, mode="request", target_root=str(tmp_path / "mine"), request="수량을 받아 총액을 낸다",
                        name="총액", max_rounds=6, confirm_at="end")
    assert run["confirm_at"] == "end"
    monkeypatch.setenv("FAKE_RUN", run["id"])
    r = runner.Runner(settings, run["id"], exe=[sys.executable, str(fake)])
    out = {}
    t = threading.Thread(target=lambda: out.update(r.run()))
    t.start()
    for _ in range(900):
        if inbox.load_run(settings, run["id"])["status"] == "waiting_approval":
            break
        time.sleep(0.2)
    got = inbox.load_run(settings, run["id"])
    assert got["status"] == "waiting_approval" and (tmp_path / "mine" / "app.py").is_file()  # 이미 구현까지 했다
    conn, _ = project.load(settings, run["project_id"])
    st = port.status_(conn, run["port_id"])
    assert [k for k, v in st["gates"].items() if not v] == [port.GATE_HUMAN]  # 남은 건 사람 승인뿐
    evs, _ = inbox.events(settings, run["id"])
    assert sum(1 for e in evs if e["kind"] in ("waiting_approval", "waiting_plan")) == 1  # 한 번만 멈춤
    guard.approve(conn, run["port_id"], port.pending_reviews(conn, run["port_id"]), channel="desktop-app", approved_by="tester")
    t.join(timeout=300)
    assert out.get("status") == "done", out
    assert port.status_(conn, run["port_id"])["finished"]["valid"] and inbox.load_run(settings, run["id"]).get("handoff")


def test_build_commands_are_exact_lines_only(tmp_path):
    """덧붙인 인자가 실행기를 바꿀 수 있으니 정확히 그 줄만 '빌드'로 바로 허용한다."""
    c = lambda cmd: inbox.classify("Bash", {"command": cmd}, tmp_path, [])  # noqa: E731
    assert {c("yarn build"), c("go test ./..."), c("cargo test"), c("pytest"), c("npm test")} == {"build"}
    assert {c("yarn dlx evil"), c("go test -exec x"), c("pytest -p evil"), c("npm test -- --x"), c("yarn")} == {"commands"}


def test_current_model_comes_from_each_cli(tmp_path, monkeypatch):
    """'기본' 대신 실제로 쓰일 값 — 설정 파일, 없으면 최근에 실제로 쓴 모델."""
    from oss_analyzer.core import agents
    codex = tmp_path / "codex"
    day = codex / "sessions" / "2026" / "11" / "13"
    day.mkdir(parents=True)
    (day / "rollout-a.jsonl").write_text(json.dumps({"type": "turn_context", "payload": {"model": "gpt-x"}}) + "\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex))
    assert agents.Codex(exe="codex").current() == {"model": "gpt-x", "effort": None, "source": "최근 사용"}
    (codex / "config.toml").write_text('model = "gpt-y"\nmodel_reasoning_effort = "high"\n', encoding="utf-8")
    assert agents.Codex(exe="codex").current() == {"model": "gpt-y", "effort": "high", "source": "Codex 설정"}
    claude = tmp_path / "claude"
    proj = claude / "projects" / "D--x"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(json.dumps({"message": {"model": "claude-sonnet-9-1"}}) + "\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    cur = agents.ClaudeCode(exe="claude").current()
    assert cur["model"] == "sonnet" and cur["full"] == "claude-sonnet-9-1" and cur["source"] == "최근 사용"


def test_call_given_as_plain_string(tmp_path):
    """실제 Codex 실행에서 나온 형식 — "call": "파일:함수" 는 대상 함수로, 함수 이름만이면 알아듣게 거절 (예전엔 내부 오류)."""
    from oss_analyzer.common.response import ToolError
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"cases": [{"name": "one", "call": "app.js:calculateOrder", "args": ["1"], "expect": {"body": 2000}}]}), encoding="utf-8")
    assert port._load_cases(f)[1][0]["call"] == {"target": "app.js:calculateOrder"}
    f.write_text(json.dumps({"cases": [{"name": "one", "call": "calculateOrder", "args": ["1"], "expect": {"body": 2000}}]}), encoding="utf-8")
    with pytest.raises(ToolError) as e:
        port._load_cases(f)
    assert "파일:함수" in e.value.message
    assert port.case_commands([{"name": "x", "call": "calculateOrder", "runtime": "node"}]) == {}
