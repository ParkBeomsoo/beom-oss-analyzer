"""공통 픽스처 — 외부 엔진 없이 임시 git 저장소 + stub 구조 지도로 순수 로직만 검증한다."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from oss_analyzer.adapters import cbm as cbm_mod
from oss_analyzer.config import Settings
from oss_analyzer.core import inventory, project

APP_PY = textwrap.dedent('''\
    import os


    def load_config(path):
        """설정 파일을 읽는다."""
        with open(path) as fh:
            return fh.read()


    def handler(request):
        cfg = load_config(os.environ["CFG"])
        return {"ok": True, "cfg": cfg}


    class Service:
        def run(self):
            return handler(None)
''')


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "src-repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "app.py").write_text(APP_PY, encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET_TOKEN=do-not-read\n", encoding="utf-8")
    (repo / "config.yml").write_text("port: 8080\n", encoding="utf-8")
    (repo / "data.weird").write_text("???\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / "home", cbm_bin=str(tmp_path / "fake-cbm"), cbm_cache=tmp_path / "cache",
        astgrep_bin=None, lease_seconds=600, unit_max_lines=1500, context_max_chars=60000, lsp_idle_seconds=60,
    )


FAKE_SYMBOLS = [
    {"qualified_name": "p.pkg.app.load_config", "name": "load_config", "label": "Function", "path": "pkg/app.py", "start_line": 4, "end_line": 7},
    {"qualified_name": "p.pkg.app.handler", "name": "handler", "label": "Function", "path": "pkg/app.py", "start_line": 10, "end_line": 12},
    {"qualified_name": "p.pkg.app.Service", "name": "Service", "label": "Class", "path": "pkg/app.py", "start_line": 15, "end_line": 17},
    {"qualified_name": "p.pkg.app.Service.run", "name": "run", "label": "Method", "path": "pkg/app.py", "start_line": 16, "end_line": 17},
    {"qualified_name": "p.tests.test_app.test_x", "name": "test_x", "label": "Function", "path": "tests/test_app.py", "start_line": 1, "end_line": 2},
    {"qualified_name": "p.env", "name": "SECRET", "label": "Function", "path": ".env", "start_line": 1, "end_line": 1},
]


@pytest.fixture
def fake_cbm(monkeypatch):
    monkeypatch.setattr(cbm_mod.CodebaseMemory, "available", property(lambda self: True))
    indexed = {}

    def fake_index(self, repo, name, exclude=None):
        indexed["exclude"] = exclude
        return {"parse_partial": {"files": [{"path": "pkg/app.py", "error_ranges": "1-1"}], "truncated": False}}

    monkeypatch.setattr(cbm_mod.CodebaseMemory, "index", fake_index)
    monkeypatch.setattr(cbm_mod.CodebaseMemory, "symbols", lambda self, name: list(FAKE_SYMBOLS))
    monkeypatch.setattr(cbm_mod.CodebaseMemory, "all_calls", lambda self, name: [{
        "caller": "p.pkg.app.handler", "caller_path": "pkg/app.py", "caller_line": 10,
        "callee": "p.pkg.app.load_config", "callee_path": "pkg/app.py", "callee_line": 4,
        "call_line": 11, "confidence": 0.95, "strategy": "lsp_direct",
    }])
    monkeypatch.setattr(inventory, "language_available", lambda lang: (lang == "python", "stub"))
    return indexed


@pytest.fixture
def opened(settings, source_repo, fake_cbm):
    out = project.open_repo(settings, str(source_repo), name="demo")
    pid = out["project"]["id"]
    conn, proj = project.load(settings, pid)
    stats = inventory.build(conn, settings, proj)
    yield conn, proj, stats
    project.close(pid)
