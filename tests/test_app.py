"""데스크톱 앱 — 화면 데이터(view)와 화면↔툴킷 연결(bridge). 창은 띄우지 않는다."""
import json

import pytest

pytest.importorskip("PySide6")

from oss_analyzer.app import view  # noqa: E402
from oss_analyzer.app.bridge import Api  # noqa: E402
from oss_analyzer.app.main import web_root  # noqa: E402
from oss_analyzer.core import inbox, make, port, project, runner  # noqa: E402


def _spec(tmp_path, settings):
    run = runner.create(settings, mode="request", target_root=str(tmp_path / "mine"), request="수량으로 총액", name="총액")
    conn, proj = project.load(settings, run["project_id"])
    make.set_requirements(conn, run["port_id"], [{"text": "총액 = 수량 x 2000"}])
    cases = tmp_path / "c.json"
    cases.write_text(json.dumps({"cases": [{"name": "a", "argv": ["1"], "req": "R1", "expect": {"equals": {"total": 2000}}}]}),
                     encoding="utf-8")
    port.capture(conn, proj, settings, run["port_id"], str(cases))
    return run, conn


def test_view_detail_shapes_spec_review(tmp_path, settings):
    run, _ = _spec(tmp_path, settings)
    d = view.detail(settings, run["id"])
    assert d["status"] == "내 차례" and d["stage"] == 1
    assert d["reviews"]["requirements"] == [{"id": "R1", "text": "총액 = 수량 x 2000"}]
    e = d["reviews"]["expectations"][0]
    assert {k: e[k] for k in ("case", "expect", "new", "source", "reqs")} == {"case": "a", "expect": {"equals": {"total": 2000}},
                                                                            "new": True, "source": None, "reqs": ["R1"]}
    assert e["input"] and e["order"] == 0  # 승인 화면에 케이스 입력이 보여야 기준을 판단할 수 있다
    labels = {g["label"] for g in d["gates"]}
    assert "모든 요구사항 구현" in labels and "요구사항마다 합격 기준" in labels
    assert d["feed"][0] == {"type": "request", "text": "수량으로 총액", "feature": None}


def test_bridge_decisions_are_recorded_in_process(tmp_path, settings):
    run, conn = _spec(tmp_path, settings)
    api = Api(settings)
    stale = json.loads(api.approveReviews(run["id"], "not-what-was-shown"))
    assert not stale["ok"] and "바뀌었습니다" in stale["error"]  # 본 것과 다르면 승인하지 않는다
    out = json.loads(api.approveReviews(run["id"], view.detail(settings, run["id"])["review_sig"]))
    assert out["ok"] and out["data"]["approved"] == 2
    payloads = [json.loads(r["payload"]) for r in conn.execute("SELECT payload FROM port_log WHERE action = 'approve'")]
    assert payloads and all(p["channel"] == "desktop-app" for p in payloads)

    assert not json.loads(api.approvePlan(run["id"], ""))["ok"]  # 계획이 없으면 거절
    r = inbox.load_run(settings, run["id"])
    r["plan"] = {"steps": [{"title": "구현", "status": "todo"}], "approved": False}
    inbox.save_run(settings, r)
    assert not json.loads(api.approvePlan(run["id"], "old"))["ok"]
    assert json.loads(api.approvePlan(run["id"], view.detail(settings, run["id"])["plan_sig"]))["ok"]
    assert inbox.load_run(settings, run["id"])["plan"]["approved"] is True

    assert json.loads(api.sendMessage(run["id"], "할인은 버림으로"))["ok"]
    msgs = inbox.load_run(settings, run["id"])["messages"]
    assert msgs[-1]["text"] == "할인은 버림으로" and msgs[-1]["delivered"] is False
    feed = view.detail(settings, run["id"])["feed"]
    assert any(f["type"] == "user" and f["text"] == "할인은 버림으로" and f.get("at") for f in feed)

    assert json.loads(api.accept(run["id"]))["ok"] and inbox.load_run(settings, run["id"])["accepted"]
    bad = json.loads(api.createRun(json.dumps({"mode": "request", "target_root": ""})))
    assert not bad["ok"] and "폴더" in bad["error"]


def test_permission_answer_through_bridge(tmp_path, settings):
    run = inbox.new_run(settings, {"mode": "request", "target_root": str(tmp_path), "name": "x", "max_rounds": 1,
                                   "project_id": None, "port_id": None})
    (inbox.run_dir(settings, run["id"]) / "inbox" / "abcdef123456.json").write_text(
        json.dumps({"id": "abcdef123456", "kind": "permission", "summary": "npm run dev"}), encoding="utf-8")
    api = Api(settings)
    assert json.loads(api.answer(run["id"], "abcdef123456", json.dumps({"allow": False})))["ok"]
    ans = json.loads((inbox.run_dir(settings, run["id"]) / "inbox" / "abcdef123456.answer.json").read_text(encoding="utf-8"))
    assert ans == {"allow": False}


def test_web_assets_are_packaged():
    root = web_root()
    for f in ("index.html", "app.css", "app.js"):
        assert (root / f).is_file()
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in html and "http" not in html.replace("http-equiv", "")


def test_every_bridge_call_from_the_page_is_a_slot(tmp_path, settings):
    """화면(app.js)이 부르는 이름은 모두 Qt 슬롯이어야 한다 — 데코레이터가 다른 함수로 밀리면 버튼이 조용히 안 먹는다."""
    import re
    from pathlib import Path
    from oss_analyzer.app import main as app_main
    js = (Path(app_main.web_root()) / "app.js").read_text(encoding="utf-8")
    names = set(re.findall(r'call\("(\w+)"', js))
    meta = Api.staticMetaObject
    slots = {bytes(meta.method(i).name()).decode() for i in range(meta.methodCount())}
    assert names and names <= slots, sorted(names - slots)
    run, _ = _spec(tmp_path, settings)
    d = json.loads(Api(settings).runDetail(run["id"]))["data"]
    assert d["running"] is False



def test_user_edits_spec_and_plan_then_approves(tmp_path, settings):
    """사용자가 앱에서 요구사항·합격 기준·계획을 직접 고치고, 고친 내용으로 승인한다. 에이전트에게도 알린다."""
    run, conn = _spec(tmp_path, settings)
    api = Api(settings)
    got = json.loads(api.specForEdit(run["id"]))["data"]
    assert got["requirements"] == [{"id": "R1", "text": "총액 = 수량 x 2000"}] and got["cases"][0]["argv"] == ["1"]
    edit = {"requirements": [{"id": "R1", "text": "총액 = 수량 x 2000"}, {"text": "0 이하면 오류"}],
            "cases": [got["cases"][0], {"name": "zero", "argv": ["0"], "req": "R2", "expect": {"status": 2}}]}
    saved = json.loads(api.saveSpec(run["id"], json.dumps(edit)))
    assert saved["ok"], saved
    d = view.detail(settings, run["id"])
    assert [r["id"] for r in d["requirements"]] == ["R1", "R2"]
    assert {e["case"] for e in d["reviews"]["expectations"]} == {"a", "zero"}
    actors = {r["actor"] for r in conn.execute("SELECT actor FROM port_map WHERE kind = 'requirement'")}
    assert any(a.startswith("user:") for a in actors)  # 사용자 작성으로 원장에 남는다
    assert json.loads(api.approveReviews(run["id"], saved["data"]["review_sig"]))["ok"]
    msgs = inbox.load_run(settings, run["id"])["messages"]
    assert msgs and "직접 고쳤다" in msgs[-1]["text"] and msgs[-1]["delivered"] is False

    r = inbox.load_run(settings, run["id"])
    r["plan"] = {"steps": [{"title": "구현", "files": ["a.py"], "status": "todo"}], "approved": False, "at": "x"}
    inbox.save_run(settings, r)
    sp = json.loads(api.savePlan(run["id"], json.dumps({"summary": "두 단계", "steps": [
        {"title": "계산 함수", "files": ["a.py"], "commands": ["python -m pytest"]}, {"title": "CLI", "files": ["cli.py"]}]})))
    assert sp["ok"] and len(inbox.load_run(settings, run["id"])["plan"]["steps"]) == 2
    assert json.loads(api.approvePlan(run["id"], sp["data"]["plan_sig"]))["ok"]
    assert inbox.load_run(settings, run["id"])["plan"]["approved"] is True


def test_spec_view_shows_agreed_criteria_and_what_was_verified(tmp_path, settings):
    import sys
    run, conn = _spec(tmp_path, settings)
    guard_mod = __import__("oss_analyzer.core.port_guard", fromlist=["x"])
    guard_mod.approve(conn, run["port_id"], port.pending_reviews(conn, run["port_id"]), channel="desktop-app", approved_by="tester")
    root = tmp_path / "mine"
    (root / "app.py").write_text("import json, sys\nprint(json.dumps({'total': int(sys.argv[1]) * 2000}))\n", encoding="utf-8")
    port.map_items(conn, run["port_id"], [{"item": "R1", "status": "ported", "target": "app.py:2"}])
    proj = project.load(settings, run["project_id"])[1]
    port.verify(conn, proj, settings, run["port_id"], target_cli=f'"{sys.executable}" app.py')
    sp = view.detail(settings, run["id"])["spec"]
    assert sp["approved"]["by"] == "tester" and sp["approved"]["channel"] == "desktop-app" and sp["verified_at"]
    c = sp["cases"][0]
    assert c["name"] == "a" and c["input"] == {"argv": ["1"]} and c["reqs"] == ["R1"] and c["ok"] is True
    assert c["actual"]["status"] == 0 and "2000" in c["actual"]["body"]  # 실제로 나온 결과가 남는다
    assert sp["gates_help"]


def test_rename_hide_and_api_key_check(tmp_path, settings, monkeypatch):
    run, _ = _spec(tmp_path, settings)
    api = Api(settings)
    assert json.loads(api.renameRun(run["id"], "  주문 API  "))["data"]["name"] == "주문 API"
    assert json.loads(api.archiveRun(run["id"], True))["ok"]
    assert view.summary(settings, inbox.load_run(settings, run["id"]))["archived"] is True
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    bad = json.loads(api.createRun(json.dumps({"mode": "request", "target_root": str(tmp_path / "x"), "request": "y",
                                               "agent": "claude", "auth": "api_key"})))
    assert not bad["ok"] and "ANTHROPIC_API_KEY" in bad["error"]  # 키 없이 API 키 인증을 고르면 시작 전에 알려 준다


def test_icon_file_is_a_valid_ico(tmp_path):
    from oss_analyzer.app.main import write_icon_files
    ico = write_icon_files(tmp_path)
    head = ico.read_bytes()[:6]
    assert head[:4] == b"\x00\x00\x01\x00" and int.from_bytes(head[4:6], "little") == 7 and (tmp_path / "app" / "app.png").is_file()


def test_view_cache_refreshes_when_run_changes(tmp_path, settings):
    run, _ = _spec(tmp_path, settings)
    a = view.summary(settings, inbox.load_run(settings, run["id"]))
    inbox.update_run(settings, run["id"], lambda r: r.update(name="새 이름"))
    b = view.summary(settings, inbox.load_run(settings, run["id"]))
    assert a["name"] != b["name"] == "새 이름"  # 캐시가 바뀐 내용을 놓치지 않는다


def test_attachments_are_copied_into_the_target_and_named_in_the_request(tmp_path, settings):
    doc = tmp_path / "기획서.md"
    doc.write_text("# 주문 총액\n- 10개 이상 10% 할인", encoding="utf-8")
    api = Api(settings)
    req = api._attach({"target_root": str(tmp_path / "mine"), "request": "", "attachments": [str(doc)]})
    assert (tmp_path / "mine" / ".oss" / "brief" / "기획서.md").is_file()
    assert ".oss/brief/기획서.md" in req and "먼저 모두 읽고" in req


def test_case_description_shows_in_review(tmp_path, settings):
    run = runner.create(settings, mode="request", target_root=str(tmp_path / "mine"), request="수량으로 총액", name="총액")
    conn, proj = project.load(settings, run["project_id"])
    make.set_requirements(conn, run["port_id"], [{"text": "총액 = 수량 x 2000"}])
    cases = tmp_path / "c.json"
    cases.write_text(json.dumps({"cases": [{"name": "one", "description": "수량 1개 — 2000원", "argv": ["1"], "req": "R1",
                                            "expect": {"equals": {"total": 2000}}}]}, ensure_ascii=False), encoding="utf-8")
    port.capture(conn, proj, settings, run["port_id"], str(cases))
    d = view.detail(settings, run["id"])
    assert d["reviews"]["expectations"][0]["description"] == "수량 1개 — 2000원"
    assert d["spec"]["cases"][0]["description"] == "수량 1개 — 2000원"
