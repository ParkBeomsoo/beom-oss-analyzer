from oss_analyzer.core.classify import classify
from oss_analyzer.core.units import _split_file, build_units


def test_classify_categories():
    assert classify("src/main.rs")[0] == "code"
    assert classify("src/main.rs")[1] == "rust"
    assert classify("tests/test_app.py")[0] == "test"
    assert classify("pkg/foo_test.go")[0] == "test"
    assert classify(".env.prd")[0] == "secret"
    assert classify("config/.env.example")[0] == "asset" or classify("config/.env.example")[0] != "secret"
    assert classify("docs/guide.md")[0] == "doc"
    assert classify("vendor/lib/x.go")[0] == "vendored"
    assert classify("static/logo.png")[0] == "binary"
    assert classify("Cargo.toml")[0] == "asset"
    assert classify("src/static/sql/event.sql")[0] == "asset"
    assert classify("api/x.pb.go")[0] == "generated"
    assert classify("data.weird")[0] == "unclassified"
    assert classify("docs/accounts.local.md")[0] == "secret"


def test_split_file_covers_every_line_and_respects_symbol_boundaries():
    symbols = [{"start_line": s, "end_line": s + 299} for s in range(1, 3001, 300)]
    pieces = _split_file("big.py", 3000, symbols, 1000)
    covered = []
    for p in pieces:
        covered.extend(range(p["start_line"], p["end_line"] + 1))
        assert p["end_line"] - p["start_line"] + 1 <= 1000
    assert covered == list(range(1, 3001))
    # 경계는 심볼 시작 직전에서만 잘린다
    starts = {s["start_line"] for s in symbols}
    assert all(p["start_line"] in starts for p in pieces)


def test_split_file_without_symbols_hard_cuts():
    pieces = _split_file("big.txt", 2500, [], 1000)
    assert [(p["start_line"], p["end_line"]) for p in pieces] == [(1, 1000), (1001, 2000), (2001, 2500)]


def test_build_units_is_deterministic():
    files = [
        {"path": "a/x.py", "category": "code", "lines": 400},
        {"path": "a/y.py", "category": "code", "lines": 700},
        {"path": "a/z.py", "category": "code", "lines": 600},
        {"path": "b/big.py", "category": "code", "lines": 4000},
        {"path": "a/README.md", "category": "doc", "lines": 10},
    ]
    u1 = build_units(files, {}, 1500)
    u2 = build_units(list(reversed(files)), {}, 1500)
    assert [u["id"] for u in u1] == [u["id"] for u in u2]
    assert all(u["line_count"] <= 1500 for u in u1)
    paths = {m["path"] for u in u1 for m in u["members"]}
    assert "a/README.md" not in paths
