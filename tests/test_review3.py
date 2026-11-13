"""세 번째 리뷰에서 나온 문제들 — 다시 생기지 않게."""
import json
import sys
import time
import zipfile

import pytest

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import docs, inbox, runner, screens, storage


def test_restore_accepts_only_real_trash_names(settings):
    for bad in ["run_0123456789ab-x/../../../etc", "../x", "run_0123456789ab", "run_0123456789ab-2026"]:
        with pytest.raises(ToolError):
            storage.restore_run(settings, bad)


def test_screen_file_must_be_inside_the_target(tmp_path):
    (tmp_path / "a.html").write_text("x", encoding="utf-8")
    spec = screens.page_spec({"file": "a.html"}, None, tmp_path, "o.png", file_base="http://127.0.0.1:9")
    assert spec["url"] == "http://127.0.0.1:9/a.html"  # file:// 이 아니라 대상 폴더만 내보내는 주소
    for bad in ["../secret.txt", str(tmp_path.parent / "x.html")]:
        with pytest.raises(ToolError):
            screens.page_spec({"file": bad}, None, tmp_path, "o.png", file_base="http://127.0.0.1:9")


def test_served_folder_does_not_leak_outside(tmp_path):
    import urllib.error
    import urllib.request
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    with screens.serve_folder(tmp_path / "site") as s:
        assert urllib.request.urlopen(s.base + "/index.html", timeout=5).read() == b"ok"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(s.base + "/../secret.txt", timeout=5)


def test_edit_notes_do_not_wake_the_runner(settings):
    run = inbox.new_run(settings, {"mode": "request", "target_root": "."})
    r = runner.Runner(settings, run["id"], exe=[sys.executable, "-c", "pass"])
    inbox.update_run(settings, run["id"], lambda x: x.setdefault("messages", []).append({"text": "고쳤다", "delivered": False, "from": "edit"}))
    assert r._new_messages() is False  # 앱에서 직접 고친 알림은 다음 라운드에 함께 전할 뿐, 승인 대기를 깨우지 않는다
    inbox.update_run(settings, run["id"], lambda x: x["messages"].append({"text": "이렇게 바꿔", "delivered": False}))
    assert r._new_messages() is True


def test_entity_declaration_hidden_in_utf16_is_refused(tmp_path):
    xml = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "a">]><w:document xmlns:w="w"/>'.encode("utf-16")
    p = tmp_path / "x.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", xml)
    with pytest.raises(ToolError):
        docs.to_text(p)


def test_stall_and_hard_cap_constants():
    assert runner.STALL_ROUNDS >= 2 and runner.HARD_ROUNDS >= 50


def test_shortcut_script_takes_paths_from_the_environment():
    import inspect
    from oss_analyzer.core import shortcut
    src = inspect.getsource(shortcut.create)
    assert "$env:OSS_LNK" in src and "_ps_quote(" not in src  # 경로가 스크립트 글자로 들어가지 않는다


def test_screen_cases_need_an_isolated_copy_on_the_source_side():
    from oss_analyzer.core import port
    assert port._needs_exec([{"name": "a", "screen": {"file": "index.html"}}], None, None) is True


def test_served_folder_and_brief_refuse_symlinks_out(tmp_path, settings):
    import os
    import urllib.error
    import urllib.request
    site = tmp_path / "site"
    site.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        os.symlink(tmp_path / "secret.txt", site / "leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("이 시스템에서는 심볼릭 링크를 만들 수 없음")
    with screens.serve_folder(site) as s:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(s.base + "/leak.txt", timeout=5)
    from oss_analyzer.app.bridge import Api
    run = inbox.new_run(settings, {"mode": "plan", "target_root": str(site)})
    brief = site / ".oss" / "brief"
    brief.mkdir(parents=True)
    os.symlink(tmp_path / "secret.txt", brief / "기획서.md")
    out = json.loads(Api(settings).readBrief(run["id"]))
    assert not out["ok"]
