"""화면 케이스 — 페이지를 실제로 띄워 글자·요소·스크립트 오류를 보고 스크린샷을 남긴다."""
import json

import pytest

from oss_analyzer.core import make, port, project, runner, screens

pytestmark = pytest.mark.skipif(not screens.available(), reason="앱 구성요소(PySide6 WebEngine) 없음")


def test_screen_cases_check_the_page_and_keep_a_screenshot(tmp_path, settings):
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("<html><head><title>주문</title></head><body><h1>주문 총액</h1>"
                                     "<form><input value='3'><button>계산하기</button></form></body></html>", encoding="utf-8")
    run = runner.create(settings, mode="request", target_root=str(root), request="주문 화면", name="주문")
    conn, proj = project.load(settings, run["project_id"])
    make.set_requirements(conn, run["port_id"], [{"text": "첫 화면에 주문 총액과 계산 버튼이 보인다"}])
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [
        {"name": "home", "description": "첫 화면", "screen": {"file": "index.html"}, "req": "R1",
         "expect": {"visible": ["주문 총액"], "selectors": ["form button"], "no_errors": True}},
        {"name": "missing", "description": "없는 글", "screen": {"file": "index.html", "mobile": True}, "req": "R1",
         "expect": {"visible": ["회원가입"]}},
    ]}, ensure_ascii=False), encoding="utf-8")
    port.capture(conn, proj, settings, run["port_id"], str(cases))
    port.map_items(conn, run["port_id"], [{"item": "R1", "status": "ported", "target": "index.html:1"}])
    out = port.verify(conn, proj, settings, run["port_id"], target_cli="echo")
    rows = {r["name"]: r for r in port._last_check(conn, run["port_id"], "differential:%")["detail"]["rows"]}
    assert rows["home"]["ok"] is True and rows["home"]["screenshot"].endswith("home.png"), rows
    assert rows["missing"]["ok"] is False and "회원가입" in rows["missing"]["diffs"][0]
    from pathlib import Path
    assert Path(rows["home"]["screenshot"]).stat().st_size > 1000 and out["cases"] == 2
