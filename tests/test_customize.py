"""확장 포인트 탐지 · 영향도(호출/인터페이스/이름 갈래) · 지문(이름 변경 탐지) — 엔진 없이 원장을 직접 채워 검증."""
from pathlib import Path

from oss_analyzer.core import customize, extpoints, ledger

BASE_PY = '''from abc import ABC, abstractmethod
RULES = {}


class Rule(ABC):
    @abstractmethod
    def apply(self, x): ...


def register_rule(cls):
    RULES[cls.__name__] = cls()
    return cls
'''
IMPL_PY = '''from base import Rule, register_rule


@register_rule
class Half(Rule):
    def apply(self, x):
        return x // 2
'''
CORE_PY = '''import os
from base import RULES

LIMIT = int(os.environ.get("LIMIT", "10"))


def total(x):
    for r in RULES.values():
        x = r.apply(x)
    return x


def handler(req):
    return total(req)
'''

SYMBOLS = [  # (id, 이름, 종류, 경로, 시작, 끝)
    ("s_rule", "Rule", "Class", "base.py", 5, 7),
    ("s_rule_apply", "apply", "Method", "base.py", 7, 7),
    ("s_reg", "register_rule", "Function", "base.py", 10, 12),
    ("s_half", "Half", "Class", "impl.py", 5, 7),
    ("s_half_apply", "apply", "Method", "impl.py", 6, 7),
    ("s_total", "total", "Function", "core.py", 7, 10),
    ("s_handler", "handler", "Function", "core.py", 13, 14),
]


def _setup(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    files = {"base.py": BASE_PY, "impl.py": IMPL_PY, "core.py": CORE_PY}
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    conn = ledger.connect(tmp_path / "ledger.db")
    proj = {"id": "p", "repo_dir": str(repo), "commit_sha": "c" * 40}
    conn.executemany("INSERT INTO file (path, category, language, lines) VALUES (?, 'code', 'python', 1)", [(n,) for n in files])
    conn.executemany("INSERT INTO symbol (id, qualified_name, name, label, path, start_line, end_line, unit_id) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, 'u1')",
                     [(i, f"p.{p[:-3]}.{n}" if lbl != "Method" else f"p.{p[:-3]}.X.{n}", n, lbl, p, a, b)
                      for i, n, lbl, p, a, b in SYMBOLS])
    conn.execute("INSERT INTO call_edge (caller, caller_path, caller_line, callee) VALUES ('p.core.handler', 'core.py', 13, 'p.core.total')")
    conn.execute("INSERT INTO feature (id, kind, name, path, start_line, end_line, source, method, full_path) "
                 "VALUES ('f1', 'http', '/h', 'core.py', 12, 12, 't', 'GET', '/h')")
    files_meta = [{"path": n, "category": "code", "language": "python"} for n in files]
    return conn, proj, files_meta


def test_extension_points_detected(tmp_path):
    conn, proj, files = _setup(tmp_path)
    out = extpoints.build(conn, proj, files)
    assert out["by_kind"] == {"interface": 1, "registry": 1, "config-key": 1}
    items = {i["kind"]: i for i in extpoints.list_(conn)["items"]}
    assert [x["name"] for x in items["interface"]["implementations"]] == ["Half"]
    assert items["registry"]["usages"] == [{"path": "impl.py", "line": 4}]
    assert items["config-key"]["name"] == "LIMIT"


def test_impact_follows_interface_and_name_match(tmp_path):
    conn, proj, files = _setup(tmp_path)
    extpoints.build(conn, proj, files)
    out = customize.impact(conn, proj, ["impl.py:7"])  # Half.apply 본문 줄
    got = {i["symbol_id"]: i for i in out["impacted"]}
    assert got["s_rule_apply"]["via"] == "interface-method"
    assert got["s_total"]["via"] == "name" and got["s_total"]["possible"]   # r.apply(x) — 간선 없는 동적 호출
    assert got["s_handler"]["via"] == "call"
    assert out["features"][0]["path"] == "/h" and out["features"][0]["possible"]


def test_impact_reports_gap_for_uncalled_target(tmp_path):
    conn, proj, _ = _setup(tmp_path)
    out = customize.impact(conn, proj, ["register_rule"], name_match=False)
    assert out["gaps"] and out["gaps"][0]["symbol_id"] == "s_reg"


def test_fingerprint_detects_rename_but_not_body_change(tmp_path):
    proj = {"id": "p", "repo_dir": str(tmp_path), "commit_sha": "x"}
    (tmp_path / "a.py").write_text("def old_name(a):\n    return a + 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def new_name(a):\n    return a + 1\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("def old_name(a):\n    return a + 2\n", encoding="utf-8")
    fa = customize.fingerprint(proj, {"path": "a.py", "name": "old_name", "start_line": 1, "end_line": 2})
    fb = customize.fingerprint(proj, {"path": "b.py", "name": "new_name", "start_line": 1, "end_line": 2})
    fc = customize.fingerprint(proj, {"path": "c.py", "name": "old_name", "start_line": 1, "end_line": 2})
    assert fa["body_anon"] == fb["body_anon"] and fa["body"] != fb["body"]
    assert fa["signature"] == fc["signature"] == "def old_name(a)" and fa["body"] != fc["body"]


def test_route_key_normalizes_params():
    assert customize._route_key({"method": "GET", "full_path": "/a/{id}/", "name": ""}) == ("GET", "/a/{}")
    assert customize._route_key({"method": None, "full_path": None, "name": '"/b/:x"'}) == ("*", "/b/{}")
