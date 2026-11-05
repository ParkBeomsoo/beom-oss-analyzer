"""에이전트 연동 — 스킬 사본의 소유·버전 확인, 사용자 스킬은 덮어쓰지 않음, 되돌리기 (Claude 설정 폴더는 임시)."""
from oss_analyzer.core import integration


def test_skill_install_outdated_foreign_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    dst = integration.skill_destination()
    assert integration.skill_status()["state"] == "missing"

    out = integration.install_skill(tmp_path / "backups")
    assert out["ok"] and out["state"] == "current" and (dst / "SKILL.md").exists() and (dst / "CLI.md").exists()
    assert integration.install_skill(tmp_path / "backups")["skipped"] == "이미 최신"

    # 설치된 사본을 손으로 고치면(또는 툴킷이 업데이트되면) outdated → 다시 설치하면 백업 후 교체
    (dst / "SKILL.md").write_text("old", encoding="utf-8")
    assert integration.skill_status()["state"] == "outdated"
    again = integration.install_skill(tmp_path / "backups")
    assert again["ok"] and again["backup"] and (tmp_path / "backups").exists()
    assert not str(again["backup"]).startswith(str(dst.parent))  # 백업은 스킬 폴더 밖

    assert integration.remove_skill()["ok"] and not dst.exists()

    # 사용자가 직접 만든 같은 이름의 스킬 — 덮어쓰지도 지우지도 않는다
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("mine", encoding="utf-8")
    assert integration.skill_status()["state"] == "foreign"
    assert integration.install_skill(tmp_path / "backups")["ok"] is False
    assert integration.remove_skill()["ok"] is False
    assert (dst / "SKILL.md").read_text(encoding="utf-8") == "mine"


def test_bundled_skill_has_no_personal_paths():
    text = "\n".join(p.read_text(encoding="utf-8") for p in integration.skill_files(integration.bundled_skill()))
    for bad in (":\\\\Users", ":/Users", "D:/", "D:\\\\", "/home/", ".venv/Scripts"):
        assert bad not in text, bad


def test_mcp_command_uses_running_interpreter():
    import sys

    assert integration.mcp_command()[0] == sys.executable


def test_plugin_manifest_matches_package():
    import json
    from pathlib import Path

    from oss_analyzer import __version__

    root = Path(__file__).resolve().parents[1]
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["version"] == __version__ == market["plugins"][0]["version"]
    assert plugin["mcpServers"]["oss-analyzer"]["args"][-1] == __version__  # 실행기가 버전 차이를 경고
    assert (root / plugin["skills"] / "oss-analyze" / "SKILL.md").exists()
    assert (root / "plugin" / "launch_mcp.py").exists()


def test_project_scope_skill_destination(tmp_path):
    dst = integration.skill_destination("project", tmp_path)
    assert dst == tmp_path.resolve() / ".claude" / "skills" / "oss-analyze"
    assert integration.install_skill(tmp_path / "b", dst)["ok"] and integration.skill_status(dst)["state"] == "current"
