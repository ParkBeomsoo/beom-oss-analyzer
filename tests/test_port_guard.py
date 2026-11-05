"""포팅 지킴이 — AI 혼자 승인 불가, 검증 뒤 코드 변경·케이스 축소·원장/골든 변조를 게이트가 잡는지."""
import json
import sys
from pathlib import Path

import pytest

from oss_analyzer import cli
from oss_analyzer.common.response import ToolError
from oss_analyzer.core import ledger, port
from oss_analyzer.core import port_guard as guard

CALC_PY = '''import json
import sys


def main(argv):
    nums = [int(a) for a in argv]
    print(json.dumps({"sum": sum(nums), "count": len(nums)}))


main(sys.argv[1:])
'''
GOOD = "import json, sys\nn = list(map(int, sys.argv[1:]))\nprint(json.dumps({'sum': sum(n), 'count': len(n)}))\n"
PY = f'"{sys.executable}"'


def _setup(tmp_path: Path, settings, target_code: str = GOOD, cases=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(CALC_PY, encoding="utf-8")
    conn = ledger.connect(tmp_path / "l.db")
    proj = {"id": "calc-1", "repo_dir": str(repo), "commit_sha": "c" * 40}
    conn.execute("INSERT INTO file (path, category, language, lines) VALUES ('calc.py', 'code', 'python', 11)")
    conn.execute("INSERT INTO symbol (id, qualified_name, name, label, path, start_line, end_line) "
                 "VALUES ('s_main', 'calc-1.calc.main', 'main', 'Function', 'calc.py', 5, 7)")
    conn.execute("INSERT INTO license_finding (path, license, category, source, line, scope) "
                 "VALUES ('LICENSE', 'MIT', 'permissive', 'text', 1, 'dir:')")
    target = tmp_path / "mine"
    target.mkdir()
    (target / "calc2.py").write_text(target_code, encoding="utf-8")
    pid = port.create(conn, proj, name="calc", targets=["main"], target_language="python", target_root=str(target))["port_id"]
    port.map_items(conn, pid, [{"item": "s_main", "status": "ported", "target": "calc2.py:2"}])
    cf = tmp_path / "cases.json"
    cf.write_text(json.dumps({"cases": cases or [{"name": "two", "argv": ["1", "2"]}, {"name": "none", "argv": []}]}),
                  encoding="utf-8")
    port.capture(conn, proj, settings, pid, str(cf), source_cli=f"{PY} -m coverage run --append --data-file=.cov calc.py",
                 trusted=True, coverage_cmd=f"{PY} -m coverage json --data-file=.cov -o cov.json", coverage_report="cov.json")
    return conn, proj, pid, target, cf


def _gates(conn, pid):
    return port.status_(conn, pid)["gates"]


def test_full_pass_then_code_change_invalidates_finish(tmp_path, settings):
    conn, proj, pid, target, _ = _setup(tmp_path, settings)
    with pytest.raises(ToolError) as e:
        port.finish(conn, pid)  # 검증 전 — 완료 불가
    assert e.value.code == "gates_unmet" and "port-verify" in e.value.recovery
    v = port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py")
    assert v["failed"] == 0 and v["evidence_id"]
    st = port.status_(conn, pid)
    assert st["complete"], st["gates"]
    assert "port-finish" in st["next_step"]
    fin = port.finish(conn, pid)
    assert port.status_(conn, pid)["finished"]["valid"] is True
    # 완료 뒤 코드를 바꾸면 — 검증·완료 판정이 무효
    (target / "calc2.py").write_text(GOOD + "# 수정\n", encoding="utf-8")
    st = port.status_(conn, pid)
    assert st["gates"][port.GATE_DIFF] is False and "바뀜" in st["differential"]["why"]
    assert st["finished"] == {"id": fin["finished"], "at": st["finished"]["at"], "valid": False}


def test_agent_cannot_approve_and_approval_binds_to_content(tmp_path, settings):
    bad = "import json, sys\nn = list(map(int, sys.argv[1:]))\nprint(json.dumps({'sum': sum(n), 'count': 0}))\n"
    conn, proj, pid, _, _ = _setup(tmp_path, settings, target_code=bad)
    port.accept(conn, pid, ["two"], "count 는 우리 쪽에서 안 씀", actor="agent")
    v = port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py")
    assert v["failed"] == 0 and v["accepted_unapproved"] == ["two"] and v["evidence_id"] is None
    assert _gates(conn, pid)[port.GATE_HUMAN] is False
    assert "AI 가 대신 승인할 수 없음" in port.status_(conn, pid)["next_step"]
    # actor="user" 라고 적어도 승인이 아님
    port.accept(conn, pid, ["two"], "count 는 우리 쪽에서 안 씀", actor="user")
    assert _gates(conn, pid)[port.GATE_HUMAN] is False
    with pytest.raises(ValueError):
        guard.approve(conn, pid, port.pending_reviews(conn, pid), channel="agent")
    # 사람 채널로 승인 → 통과, 다시 verify 하면 근거 발급
    guard.approve(conn, pid, port.pending_reviews(conn, pid), channel="cli-tty", approved_by="tester")
    assert _gates(conn, pid)[port.GATE_HUMAN] is True
    assert port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py")["evidence_id"]
    # 승인 뒤 사유를 바꾸면(= 다른 내용) 다시 승인 필요
    port.accept(conn, pid, ["two"], "다른 사유")
    port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py")
    assert _gates(conn, pid)[port.GATE_HUMAN] is False


def test_omitted_needs_human_approval(tmp_path, settings):
    conn, proj, pid, _, _ = _setup(tmp_path, settings)
    port.map_items(conn, pid, [{"item": "s_main", "status": "omitted", "note": "안 씀"}])
    reviews = port.pending_reviews(conn, pid)
    assert [r["kind"] for r in reviews] == ["omitted"]
    assert _gates(conn, pid)[port.GATE_HUMAN] is False


def test_tampering_is_detected(tmp_path, settings):
    conn, proj, pid, _, _ = _setup(tmp_path, settings)
    port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py")
    assert _gates(conn, pid)[port.GATE_INTEGRITY] is True
    # 골든을 손으로 고침 → verify 가 거부
    cap = port._last_check(conn, pid, "capture")
    gf = Path(cap["detail"]["golden"])
    data = json.loads(gf.read_text(encoding="utf-8"))
    data["results"][0]["body"] = {"sum": 999}
    gf.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ToolError) as e:
        port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py")
    assert e.value.code == "tampered"
    # 원장을 툴킷 밖에서 고침 → 기록 무결 게이트 실패
    conn.execute("UPDATE port_map SET note = '몰래' WHERE port_id = ?", (pid,))
    st = port.status_(conn, pid)
    assert st["gates"][port.GATE_INTEGRITY] is False and "기록이 툴킷 밖에서" in st["next_step"]
    conn.execute("UPDATE port_map SET note = NULL WHERE port_id = ?", (pid,))
    assert _gates(conn, pid)[port.GATE_INTEGRITY] is True
    last = port._last_check(conn, pid, "differential:%")
    conn.execute("UPDATE port_check SET failed = 0, passed = 99 WHERE id = ?", (last["id"],))
    assert _gates(conn, pid)[port.GATE_INTEGRITY] is False


def test_dropping_cases_needs_approval_and_cannot_be_laundered(tmp_path, settings):
    conn, proj, pid, _, cf = _setup(tmp_path, settings)
    one = tmp_path / "one.json"
    one.write_text(json.dumps({"cases": [{"name": "none", "argv": []}]}), encoding="utf-8")
    for _ in range(2):  # 두 번 연속 capture 해도 기준은 승인된 케이스 목록 그대로
        out = port.capture(conn, proj, settings, pid, str(one), source_cli=f"{PY} calc.py", trusted=True)
        assert out["case_change"]["removed"] == ["two"]
    assert [r["kind"] for r in port.pending_reviews(conn, pid)] == ["case_change"]
    assert _gates(conn, pid)[port.GATE_HUMAN] is False


def test_live_verify_with_fewer_cases_fails_gate(tmp_path, settings):
    conn, proj, pid, _, _ = _setup(tmp_path, settings)
    one = tmp_path / "one.json"
    one.write_text(json.dumps({"cases": [{"name": "none", "argv": []}]}), encoding="utf-8")
    v = port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py", cases_file=str(one),
                    source_cli=f"{PY} calc.py", trusted=True)
    assert v["failed"] == 0
    st = port.status_(conn, pid)
    assert st["gates"][port.GATE_DIFF] is False and st["differential"]["cases"] == ["two"]


def test_target_coverage_is_fallback_for_adequacy(tmp_path, settings):
    conn, proj, pid, target, cf = _setup(tmp_path, settings)
    # 원본 계측 없이 다시 capture → 원본 커버리지는 이 capture 기준 미측정
    port.capture(conn, proj, settings, pid, str(cf), source_cli=f"{PY} calc.py", trusted=True)
    assert _gates(conn, pid)[port.GATE_ADEQUACY] is False
    v = port.verify(conn, proj, settings, pid, target_cli=f"{PY} -m coverage run --append --data-file=.tcov calc2.py",
                    target_coverage_cmd=f"{PY} -m coverage json --data-file=.tcov -o tcov.json",
                    target_coverage_report="tcov.json")
    assert v["target_adequacy"]["executed"] == 1, v["target_adequacy"]
    st = port.status_(conn, pid)
    assert st["gates"][port.GATE_ADEQUACY] is True and st["adequacy"]["basis"] == "대상 커버리지"


def test_cli_approve_refuses_without_terminal(tmp_path, settings, monkeypatch, capsys):
    bad = "import json, sys\nprint(json.dumps({'sum': 0, 'count': 0}))\n"
    conn, proj, pid, _, _ = _setup(tmp_path, settings, target_code=bad)
    port.accept(conn, pid, ["*"], "데모")
    port.verify(conn, proj, settings, pid, target_cli=f"{PY} calc2.py")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(ToolError) as e:
        cli._port_approve(conn, pid, list_only=False)
    assert e.value.code == "human_required"
    assert cli._port_approve(conn, pid, list_only=True)["pending"]
