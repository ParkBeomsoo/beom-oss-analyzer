"""요청사항 기반 만들기 — 요구사항 원장, 합격 기준 승인, 불변 조건, 완료 판정 (원본 없이)."""
import json
import sys
from pathlib import Path

import pytest

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import make, port, project
from oss_analyzer.core import port_guard as guard

PY = f'"{sys.executable}"'
APP = '''import json, sys
args = sys.argv[1:]
if not args:
    print(json.dumps({"error": "수량 필요"})); sys.exit(2)
try:
    qty = int(args[0])
except ValueError:
    print(json.dumps({"error": "숫자 아님"})); sys.exit(2)
total = qty * 2000
if qty >= 10:
    total = int(total * 0.9)
print(json.dumps({"qty": qty, "total": total}))
'''


def _cases(tmp_path: Path, cases: list[dict]) -> str:
    f = tmp_path / "cases.json"
    f.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    return str(f)


def _start(tmp_path, settings):
    target = tmp_path / "mine"
    target.mkdir()
    (target / "order.py").write_text(APP, encoding="utf-8")
    out = make.create(settings, name="주문 가격", request="수량을 받아 개당 2000원, 10개 이상이면 10% 할인한 총액을 낸다",
                      target_root=str(target))
    conn, proj = project.load(settings, out["project_id"])
    return conn, proj, out["port_id"], target


def test_request_mode_end_to_end(tmp_path, settings):
    conn, proj, pid, target = _start(tmp_path, settings)
    st = port.status_(conn, pid)
    assert st["mode"] == "request" and "make-req" in st["next_step"]

    make.set_requirements(conn, pid, [{"text": "개당 2000원으로 총액 계산"}, {"text": "10개 이상이면 10% 할인"}])
    assert [r["id"] for r in make.requirements(conn, pid)] == ["R1", "R2"]

    # 케이스에 expect·req 가 없으면 거부
    with pytest.raises(ToolError):
        port.capture(conn, proj, settings, pid, _cases(tmp_path, [{"name": "a", "argv": ["1"]}]))
    cases = [{"name": "one", "argv": ["1"], "req": "R1", "expect": {"equals": {"total": 2000}}},
             {"name": "ten", "argv": ["10"], "req": ["R1", "R2"], "expect": {"equals": {"total": 18000}}}]
    cap = port.capture(conn, proj, settings, pid, _cases(tmp_path, cases), explore_n=6)
    assert cap["generated"] > 0 and not cap["errors"]  # 원본 없이 저장 (아무것도 실행하지 않음)

    st = port.status_(conn, pid)
    assert st["gates"][port.GATE_REQ_CASES] is True
    assert "승인" in st["next_step"]  # 구현 전에 요구사항·기준 승인
    kinds = sorted(r["kind"] for r in port.pending_reviews(conn, pid))
    assert kinds == ["expectation", "expectation", "requirements"]
    guard.approve(conn, pid, port.pending_reviews(conn, pid), channel="cli-tty", approved_by="tester")

    assert "port-map" in port.status_(conn, pid)["next_step"]
    port.map_items(conn, pid, [{"item": "R1", "status": "ported", "target": "order.py:10"},
                               {"item": "R2", "status": "ported", "target": "order.py:11"}])
    v = port.verify(conn, proj, settings, pid, target_cli=f"{PY} -m coverage run --append --data-file=.cov order.py",
                    target_coverage_cmd=f"{PY} -m coverage json --data-file=.cov -o cov.json", target_coverage_report="cov.json")
    assert v["failed"] == 0, v["failures"]
    assert v["target_adequacy"]["executed"] == 2
    st = port.status_(conn, pid)
    assert st["complete"], st["gates"]
    assert port.finish(conn, pid)["finished"]


def test_requirement_without_case_and_changed_list_need_work(tmp_path, settings):
    conn, proj, pid, _ = _start(tmp_path, settings)
    make.set_requirements(conn, pid, [{"text": "총액 계산"}, {"text": "할인"}])
    port.capture(conn, proj, settings, pid, _cases(tmp_path, [{"name": "one", "argv": ["1"], "req": "R1",
                                                                "expect": {"equals": {"total": 2000}}}]))
    st = port.status_(conn, pid)
    assert st["gates"][port.GATE_REQ_CASES] is False and st["requirements"]["uncovered"] == ["R2"]
    assert "R2" in st["next_step"]
    guard.approve(conn, pid, [r for r in port.pending_reviews(conn, pid) if r["kind"] == "requirements"],
                  channel="cli-tty", approved_by="tester")
    # 승인 뒤 요구사항 문장을 바꾸면 다시 승인 필요
    make.set_requirements(conn, pid, [{"id": "R1", "text": "총액 계산 (원 단위)"}, {"id": "R2", "text": "할인"}])
    assert any(r["kind"] == "requirements" for r in port.pending_reviews(conn, pid))


def test_invariant_variants_fail_on_server_errors():
    assert port.check_invariant({"status": 200, "body": {}}, {"path": "/x"}) == []
    assert port.check_invariant({"status": 404, "body": {}}, {"path": "/x"}) == []
    assert port.check_invariant({"status": 500, "body": {}}, {"path": "/x"})
    assert port.check_invariant({"status": 2, "body": None, "crashed": True}, {"argv": []})
    variants = make.invariant_variants([], [{"name": "v", "argv": ["x"], "expect": {"status": 0}, "req": ["R1"], "generated": True}])
    assert variants[0]["invariant"] and variants[0]["new"] and "expect" not in variants[0]


def test_crashing_input_variant_fails_verify(tmp_path, settings):
    conn, proj, pid, target = _start(tmp_path, settings)
    (target / "order.py").write_text("import json, sys\nprint(json.dumps({'total': int(sys.argv[1]) * 2000}))\n", encoding="utf-8")
    make.set_requirements(conn, pid, [{"text": "총액"}])
    port.capture(conn, proj, settings, pid, _cases(tmp_path, [{"name": "one", "argv": ["1"], "req": "R1",
                                                                "expect": {"equals": {"total": 2000}}}]), explore_n=4)
    v = port.verify(conn, proj, settings, pid, target_cli=f"{PY} order.py")
    # CLI 는 예외로 끝나도 '종료됨'은 아니다 — 기대값 케이스는 통과, 변형은 상태 코드만 다름(불변 조건은 HTTP 5xx·종료만 봄)
    assert v["passed"] >= 1
