"""포팅 — 비교 규칙, 케이스 템플릿, 매핑 게이트, CLI 경계 차등 검증 (서버·엔진 없이)."""
import sys
from pathlib import Path

from oss_analyzer.core import ledger, port

CALC_PY = '''import json
import sys


def main(argv):
    nums = [int(a) for a in argv]
    print(json.dumps({"sum": sum(nums), "count": len(nums)}))


main(sys.argv[1:])
'''


def test_compare_ignore_match_and_status_only():
    src = {"status": 200, "body": {"id": "a" * 32, "total": 10, "items": [{"x": 1, "t": 5}]}}
    dst = {"status": 200, "body": {"id": "b" * 32, "total": 10, "items": [{"x": 1, "t": 9}]}}
    assert port.compare(src, dst, {"match": {"id": "[a-f]{32}"}, "ignore": ["items.*.t"]}) == []
    assert port.compare(src, dst, {"match": {"id": "[a-f]{32}"}})  # items.t 차이
    assert port.compare(src, {"status": 200, "body": {"id": "zz"}}, {"match": {"id": "[a-f]{32}"}, "compare": "full"})
    assert port.compare({"status": 422, "body": [1]}, {"status": 422, "body": "x"}, {"compare": "status"}) == []
    err = {"status": 500, "body": "Internal Server Error"}
    assert port.compare(err, dict(err), {"match": {"id": "[a-f]{32}"}}) == []  # 양쪽 다 id 없음 = 같음
    assert port.compare(src, {"status": 200, "body": {"total": 10, "items": [{"x": 1, "t": 5}]}},
                        {"match": {"id": "[a-f]{32}"}})  # 한쪽에만 있음


def test_template_uses_previous_results():
    saved = {"create": {"id": "abc", "nested": {"n": 3}}}
    assert port._template({"path": "/o/{{create.id}}/{{ create.nested.n }}"}, saved) == {"path": "/o/abc/3"}


def _project(tmp_path: Path):
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
    return conn, proj


def test_port_gate_and_cli_differential(tmp_path, settings):
    conn, proj = _project(tmp_path)
    target = tmp_path / "mine"
    target.mkdir()
    # 대상(내 코드) 첫 판: count 를 빠뜨림
    (target / "calc2.py").write_text("import json, sys\nprint(json.dumps({'sum': sum(map(int, sys.argv[1:]))}))\n",
                                     encoding="utf-8")
    created = port.create(conn, proj, name="calc", targets=["main"], target_language="python", target_root=str(target))
    pid = created["port_id"]
    assert created["items"] == 1

    bad = port.map_items(conn, pid, [{"item": "s_main", "status": "ported", "target": "calc2.py:9"}])
    assert not bad["results"][0]["ok"] and not bad["gates"]["모든 항목 매핑됨 (pending 0)"]
    ok = port.map_items(conn, pid, [{"item": "s_main", "status": "ported", "target": "calc2.py:2"}])
    assert ok["gates"]["모든 항목 매핑됨 (pending 0)"] and ok["gates"]["ported 대상 위치가 모두 존재 (줄 내용도 그대로)"]

    cases = tmp_path / "cases.json"
    cases.write_text('{"cases": [{"name": "two", "argv": ["1", "2"]}, {"name": "none", "argv": []}]}', encoding="utf-8")
    py = f'"{sys.executable}"'
    # 원본을 커버리지로 감싸 실행 → 케이스가 슬라이스 함수를 거쳤는지(충분성)까지 기록
    cap = port.capture(conn, proj, settings, pid, str(cases), source_cli=f"{py} -m coverage run --append --data-file=.cov calc.py",
                       trusted=True, coverage_cmd=f"{py} -m coverage json --data-file=.cov -o cov.json",
                       coverage_report="cov.json")
    assert cap["cases"] == 2 and not cap["errors"]
    assert cap["adequacy"]["executed"] == 1 and not cap["adequacy"]["not_executed"]
    kw = {"target_cli": f"{py} calc2.py"}
    first = port.verify(conn, proj, settings, pid, **kw)
    assert first["failed"] == 2 and first["evidence_id"] is None

    (target / "calc2.py").write_text("import json, sys\nn = list(map(int, sys.argv[1:]))\n"
                                     "print(json.dumps({'sum': sum(n), 'count': len(n)}))\n", encoding="utf-8")
    second = port.verify(conn, proj, settings, pid, **kw)
    assert second["passed"] == 2 and second["failed"] == 0 and second["evidence_id"]
    # 대상 파일을 고쳐 매핑한 줄(2줄) 내용이 바뀜 → 게이트가 잡는다
    st = port.status_(conn, pid)
    assert not st["complete"] and st["broken_targets"][0]["error"] == "대상 줄 내용이 매핑 때와 다름"
    port.map_items(conn, pid, [{"item": "s_main", "status": "ported", "target": "calc2.py:3"}])
    st = port.status_(conn, pid)
    assert st["complete"], st["gates"]


def test_moved_target_is_followed(tmp_path):
    conn, proj = _project(tmp_path)
    target = tmp_path / "mine"
    target.mkdir()
    (target / "a.js").write_text("function main() {}\n", encoding="utf-8")
    pid = port.create(conn, proj, name="m", targets=["main"], target_language="javascript", target_root=str(target))["port_id"]
    port.map_items(conn, pid, [{"item": "s_main", "status": "ported", "target": "a.js:1"}])
    (target / "a.js").write_text("// 머리말\n\nfunction main() {}\n", encoding="utf-8")
    st = port.status_(conn, pid)
    assert st["broken_targets"][0]["moved_to"] == "a.js:3"
    out = port.follow(conn, pid)
    assert out["moved"] == [{"item": "s_main", "from": "a.js:1", "to": "a.js:3"}]
    assert port.status_(conn, pid)["broken_targets"] == []


def test_accepted_divergence_is_counted_separately(tmp_path, settings):
    conn, proj = _project(tmp_path)
    target = tmp_path / "mine"
    target.mkdir()
    (target / "c.py").write_text("import json, sys\nprint(json.dumps({'sum': 0, 'count': 0}))\n", encoding="utf-8")
    pid = port.create(conn, proj, name="a", targets=["main"], target_language="python", target_root=str(target))["port_id"]
    cases = tmp_path / "c.json"
    cases.write_text('{"cases": [{"name": "none", "argv": []}, {"name": "two", "argv": ["1", "2"]}]}', encoding="utf-8")
    py = f'"{sys.executable}"'
    port.capture(conn, proj, settings, pid, str(cases), source_cli=f"{py} calc.py", trusted=True)
    first = port.verify(conn, proj, settings, pid, target_cli=f"{py} c.py")
    assert first["failed"] == 1
    try:
        port.accept(conn, pid, ["tw*"], " ")
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "code", "") == "invalid_request"
    port.accept(conn, pid, ["tw*"], "데모: 의도적 차이")
    second = port.verify(conn, proj, settings, pid, target_cli=f"{py} c.py")
    assert second["failed"] == 0 and second["accepted"][0]["name"] == "two"
    assert any("허용" in x for x in second["limitations"])


def test_adequacy_gate_requires_coverage(tmp_path, settings):
    conn, proj = _project(tmp_path)
    pid = port.create(conn, proj, name="c", targets=["main"], target_language="python", target_root=str(tmp_path))["port_id"]
    gates = port.status_(conn, pid)["gates"]
    assert gates[port.GATE_ADEQUACY] is False  # 미측정은 통과가 아님


def test_explore_is_deterministic_and_varied():
    cases = [{"name": "c", "method": "POST", "path": "/o", "json": {"items": [{"price": 5000}], "coupon": "W"}},
             {"name": "g", "method": "GET", "path": "/o/{{c.id}}"}]
    a, b = port.explore(cases, 12), port.explore(cases, 12)
    assert a == b and len(a) == 12 and all(x["generated"] and x["from"] == "c" for x in a)
    every = port.explore(cases, 1000)
    labels = {x["name"].split("=", 1)[1] for x in every}
    assert {"삭제", "null", "0", "음수", "소수문자열", "중복", "빈문자열"} <= labels
    assert any(x["json"].get("items") == [{"price": "5000.5"}] for x in every)
    assert all("{{" not in str(x.get("json")) for x in every)


def test_function_level_call_python_vs_javascript(tmp_path, settings):
    """경계(HTTP·CLI) 없이 함수끼리 직접 비교 — 원본 Python 함수 vs 대상 JS 함수."""
    import shutil

    if not shutil.which("node"):
        import pytest
        pytest.skip("node 없음")
    conn, proj = _project(tmp_path)
    repo = Path(proj["repo_dir"])
    (repo / "price.py").write_text("def total(items):\n    return sum(int(i['price']) * int(i.get('qty', 1)) for i in items)\n",
                                   encoding="utf-8")
    target = tmp_path / "mine"
    target.mkdir()
    (target / "price.js").write_text("exports.total = (items) => items.reduce((s, i) => s + Math.trunc(Number(i.price)) * "
                                     "Math.trunc(Number(i.qty ?? 1)), 0);\n", encoding="utf-8")
    pid = port.create(conn, proj, name="f", targets=["main"], target_language="javascript", target_root=str(target))["port_id"]
    cases = tmp_path / "fn.json"
    cases.write_text('{"defaults": {"call": {"source": "price.py:total", "target": "price.js:total"}},'
                     '"cases": [{"name": "two", "args": [[{"price": 5000, "qty": 2}, {"price": 100}]]}]}', encoding="utf-8")
    cap = port.capture(conn, proj, settings, pid, str(cases), trusted=True, explore_n=40)
    assert cap["errors"] == [] and cap["generated"] > 0
    res = port.verify(conn, proj, settings, pid)
    by_name = {f["name"]: f for f in res["failures"]}
    # 원본: int("5000.5") → 예외 / 대상: Number→trunc 로 5000 — 케이스 밖 입력에서 동작이 갈라지는 것을 찾아낸다
    assert any("소수문자열" in n for n in by_name), list(by_name)
    assert "two" not in by_name


def test_source_execution_requires_trust(tmp_path, settings):
    conn, proj = _project(tmp_path)
    pid = port.create(conn, proj, name="c", targets=["main"], target_language="go", target_root=str(tmp_path))["port_id"]
    cases = tmp_path / "c.json"
    cases.write_text('[{"argv": ["1"]}]', encoding="utf-8")
    try:
        port.verify(conn, proj, settings, pid, cases_file=str(cases), source_cli="python calc.py", target_cli="x")
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "code", "") == "capability_unavailable"
    else:
        raise AssertionError("trusted 없이 원본을 실행하면 안 됨")


def test_unknown_license_blocks_gate(tmp_path):
    conn, proj = _project(tmp_path)
    conn.execute("DELETE FROM license_finding")
    sl = port.slice_(conn, proj, ["main"])
    assert "확인하지 못한" in sl["license_warning"] and "허용형이 아닌" not in sl["license_warning"]
