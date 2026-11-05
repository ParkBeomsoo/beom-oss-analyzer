"""구조적 한계 보완 — 커버리지 파서·경로 맞추기, 라이선스 판정, 엔트로피 마스킹, CPG 파일 선택."""
import pytest

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import dataflow, ledger, runtime
from oss_analyzer.core import license as lic
from oss_analyzer.core.mask import looks_random, mask_line


def test_coveragepy_and_lcov_parse():
    fmt, data = runtime.parse('{"meta": {}, "files": {"a/b.py": {"executed_lines": [1, 3], "missing_lines": [4]}}}')
    assert fmt == "coverage.py" and data["a/b.py"]["lines"] == {1: 1, 3: 1, 4: 0}
    fmt, data = runtime.parse("SF:src/x.js\nFN:3,run\nFNDA:2,run\nDA:3,2\nDA:4,0\nend_of_record\n")
    assert fmt == "lcov" and data["src/x.js"]["functions"] == [("run", 3, 2)] and data["src/x.js"]["lines"][4] == 0


def test_istanbul_and_cobertura_parse():
    ist = '{"/w/src/a.js": {"path": "/w/src/a.js", "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 3}}},' \
          ' "s": {"0": 1}, "fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1}}}}, "f": {"0": 1}}}'
    fmt, data = runtime.parse(ist)
    assert fmt == "istanbul" and data["/w/src/a.js"]["lines"] == {2: 1, 3: 1}
    fmt, data = runtime.parse('<?xml version="1.0"?><!DOCTYPE coverage SYSTEM "x.dtd"><coverage><packages><package><classes>'
                              '<class filename="pkg/m.py"><lines><line number="5" hits="0"/></lines></class>'
                              '</classes></package></packages></coverage>')
    assert fmt == "cobertura" and data["pkg/m.py"]["lines"] == {5: 0}


def test_xml_entities_rejected():
    with pytest.raises(ToolError):
        runtime.parse('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><coverage>&a;</coverage>')


def test_report_paths_mapped_to_repo():
    mapping, unmatched = runtime._match_paths(
        ["C:\\run\\src\\app\\x.py", "/work/app/y.py", "z.py", "other/q.py"],
        {"app/x.py", "app/y.py", "lib/z.py", "a/q.py", "b/q.py"}, ["C:/run/src", "/work"])
    assert mapping == {"C:\\run\\src\\app\\x.py": "app/x.py", "/work/app/y.py": "app/y.py", "z.py": "lib/z.py"}
    assert unmatched == ["other/q.py"]  # 꼬리 일치가 둘 이상이면 추측하지 않음


@pytest.mark.parametrize("expr,cat", [
    ("MIT", "permissive"), ("Apache-2.0 OR GPL-3.0", "permissive"), ("MIT AND GPL-2.0-only", "strong-copyleft"),
    ("LGPL-2.1-or-later", "weak-copyleft"), ("BUSL-1.1", "source-available"), ("Apache Software License", "permissive"),
    ("Proprietary", "unknown"),
])
def test_license_category(expr, cat):
    assert lic.category_of(expr) == cat


def test_license_text_match():
    assert lic.match_text("Permission is hereby granted, free of charge, to any person obtaining a copy") == "MIT"
    assert lic.match_text("GNU AFFERO GENERAL PUBLIC LICENSE\n Version 3") == "AGPL-3.0"
    assert lic.match_text("Redistribution and use in source and binary forms ... Neither the name of") == "BSD-3-Clause"


def test_entropy_mask_only_random_literals():
    assert mask_line('c = Client("9f3aK2mQx7Lp0zR8vT4wY6bN1cD5eH3j")', code=True)[1]
    for s in ("createOrderRequestHandler", "1.2.3-beta.4+build.567", "com.example.service.OrderService2",
              "order_not_found_for_user_id"):
        assert not looks_random(s), s
    assert not mask_line('u = "https://example.com/api/v1/orders/123456"', code=True)[1]


def test_cpg_selection_excludes_tests_and_scope(tmp_path):
    conn = ledger.connect(tmp_path / "l.db")
    conn.executemany("INSERT INTO file (path, category) VALUES (?, ?)",
                     [("app/a.py", "code"), ("app/sub/b.py", "code"), ("lib/c.py", "code"), ("tests/t.py", "test"),
                      ("README.md", "doc")])
    assert dataflow.selection(conn) == (["app/a.py", "app/sub/b.py", "lib/c.py"], 2)
    assert dataflow.selection(conn, ["app/sub"]) == (["app/sub/b.py"], 4)


def test_setup_checksum_and_zip_guard(tmp_path):
    import hashlib
    import zipfile

    from oss_analyzer.core import setup

    blob = b"engine-bytes"
    good = hashlib.sha256(blob).hexdigest()
    assert setup.verify_checksum(blob, "x.zip", f"{good}  x.zip\n{'0' * 64}  y.zip\n") == "sha256"
    assert setup.verify_checksum(blob, "x.zip", hashlib.sha512(blob).hexdigest() + "\n") == "sha512"
    with pytest.raises(ToolError):
        setup.verify_checksum(blob, "x.zip", f"{'0' * 64}  x.zip\n")
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.txt", "x")
    with zipfile.ZipFile(evil) as zf, pytest.raises(ToolError):
        setup._safe_extract(zf, tmp_path / "dest")
    assert not (tmp_path / "outside.txt").exists()
