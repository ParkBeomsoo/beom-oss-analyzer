"""인벤토리 → 루프 → 근거 검증 → 미확인 → 리포트 전체 흐름."""
import pytest

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import evidence, ledger, loop, report, unknowns


def test_inventory_classifies_every_file_and_hides_secrets(opened, fake_cbm):
    conn, proj, stats = opened
    assert fake_cbm["exclude"] == [".env"]  # 구조 지도 엔진에도 비밀 파일을 넘기지 않는다
    cats = {r["path"]: r["category"] for r in conn.execute("SELECT path, category FROM file")}
    assert cats[".env"] == "secret"
    assert cats["data.weird"] == "unclassified"
    assert cats["pkg/app.py"] == "code"
    assert cats["tests/test_app.py"] == "test"
    # 비밀 파일의 심볼은 원장에 들어가지 않는다
    assert conn.execute("SELECT COUNT(*) FROM symbol WHERE path = '.env'").fetchone()[0] == 0
    assert stats["unassigned_symbols"] == 0
    # 파싱 실패 신호 → 미확인
    domains = {r["domain"] for r in conn.execute("SELECT domain FROM unknown")}
    assert "structure" in domains


def test_evidence_validation(opened, settings):
    conn, proj, _ = opened
    with ledger.tx(conn):
        ok = evidence.validate_location(conn, proj, {"path": "pkg/app.py", "start_line": 4, "end_line": 7})
    assert ok["commit"] == proj["commit_sha"] and len(ok["snippet_hash"]) == 16
    with pytest.raises(ToolError) as e:
        evidence.validate_location(conn, proj, {"path": "pkg/app.py", "start_line": 1, "end_line": 999})
    assert e.value.code == "evidence_rejected"
    with pytest.raises(ToolError):
        evidence.validate_location(conn, proj, {"path": ".env", "start_line": 1})
    with pytest.raises(ToolError):
        evidence.validate_location(conn, proj, {"path": "pkg/app.py", "start_line": 4, "snippet_hash": "deadbeefdeadbeef"})
    with pytest.raises(ToolError):
        evidence.validate_location(conn, proj, {"path": "nope.py", "start_line": 1})


def test_loop_gates_and_levels(opened, settings):
    conn, proj, _ = opened
    first = loop.next_unit(conn, settings, "a1", kind="code")
    unit_id = first["unit"]["id"]
    # 다른 에이전트는 같은 단위를 못 받는다
    other = loop.next_unit(conn, settings, "a2", kind="code")
    assert other["unit"] is None or other["unit"]["id"] != unit_id

    ctx = loop.unit_context(conn, settings, proj, unit_id, "a1")
    assert "def load_config" in ctx["code"]
    assert ctx["calls"][0]["evidence_id"].startswith("ev_")
    sym = {s["qualified_name"]: s["id"] for s in ctx["symbols"]}

    with pytest.raises(ToolError) as e:
        loop.complete_unit(conn, unit_id=unit_id, agent="a1", summary="x")
    assert e.value.code == "gate_failed" and len(e.value.data["missing"]) == 4

    with pytest.raises(ToolError):
        loop.record_finding(conn, proj, unit_id=unit_id, agent="a1", text="근거 없음", severity="info",
                            evidence_items=[], symbol_ids=[])
    with pytest.raises(ToolError):  # 임대 안 한 에이전트
        loop.record_finding(conn, proj, unit_id=unit_id, agent="a2", text="x", severity="info",
                            evidence_items=[{"path": "pkg/app.py", "start_line": 4}], symbol_ids=[])

    r = loop.record_finding(conn, proj, unit_id=unit_id, agent="a1",
                            text="handler 는 CFG 환경변수 경로의 설정 파일을 읽어 돌려준다", severity="important",
                            evidence_items=[{"evidence_id": ctx["calls"][0]["evidence_id"]},
                                            {"path": "pkg/app.py", "start_line": 10, "end_line": 12}],
                            symbol_ids=[sym["p.pkg.app.handler"], sym["p.pkg.app.load_config"]])
    assert r["level"] == "L0" and r["review_required"]
    loop.mark_symbols(conn, unit_id=unit_id, agent="a1",
                      symbol_ids=[sym["p.pkg.app.Service"], sym["p.pkg.app.Service.run"]], note="handler 위임만 함")
    done = loop.complete_unit(conn, unit_id=unit_id, agent="a1", summary="설정 로딩 핸들러")
    assert done["level"] == "L0"
    # 목표(L1, python LSP 사용 가능) 미달 → 검증 공백 미확인 자동 생성
    assert done["verification_gap_unknown"]["created"]

    with pytest.raises(ToolError):
        loop.review_claim(conn, claim_id=r["claim_id"], reviewer="a1", verdict="confirmed", note="self")
    loop.review_claim(conn, claim_id=r["claim_id"], reviewer="rev", verdict="confirmed", note="코드 확인")

    cov = report.coverage(conn)
    assert cov["gates"]["모든 파일 분류됨"] is False  # data.weird
    assert cov["gates"]["중요 결론 독립 검토 완료"] is True
    assert cov["complete"] is False


def test_unknown_resolution_requires_level(opened):
    conn, proj, _ = opened
    with ledger.tx(conn):
        u = unknowns.record(conn, question="load_config 를 부르는 곳이 handler 뿐인가?", domain="callers", required_level="L1")
        again = unknowns.record(conn, question="load_config  를 부르는 곳이 handler 뿐인가?", domain="callers", required_level="L1")
        assert again["created"] is False
        ev = evidence.record(conn, proj, provider="agent-read", operation="read", parameters={}, result={},
                             locations=[{"path": "pkg/app.py", "start_line": 11}], confidence="observed")
    with pytest.raises(ToolError) as e:
        with ledger.tx(conn):
            unknowns.update(conn, u["unknown_id"], expected_revision=1, actor="a",
                            resolution={"disposition": "verified", "rationale": "읽어봄", "evidence_ids": [ev["evidence_id"]]})
    assert e.value.code == "gate_failed"
    with pytest.raises(ToolError) as e:
        with ledger.tx(conn):
            unknowns.update(conn, u["unknown_id"], expected_revision=7, actor="a", status="investigating")
    assert e.value.code == "revision_conflict"
    with ledger.tx(conn):
        out = unknowns.update(conn, u["unknown_id"], expected_revision=1, actor="a",
                              resolution={"disposition": "out-of-scope", "rationale": "테스트 범위 밖", "evidence_ids": []})
    assert out["status"] == "resolved"


def test_export_writes_markdown_and_json(opened, settings):
    conn, proj, _ = opened
    out = report.export(conn, settings, proj)
    md = open(out["markdown"], encoding="utf-8").read()
    assert "완료 조건" in md and proj["commit_sha"] in md
    assert "do-not-read" not in md
