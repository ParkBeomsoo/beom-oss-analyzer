"""실전 실행에서 드러난 문제의 회귀 테스트."""
import json

import pytest

from oss_analyzer import cli
from oss_analyzer.common.response import ToolError
from oss_analyzer.core import loop, mybatis
from oss_analyzer.core.mask import mask_lines

MAPPER_XML = """<?xml version="1.0"?>
<mapper namespace="com.x.repo.EventMapper">
  <sql id="cols">A, B</sql>
  <select id="getEventList" resultType="map">
    SELECT <include refid="cols"/> FROM T
    <if test="x != null">WHERE X = #{x}</if>
  </select>
  <update id="updateEvent">
    UPDATE T SET A = 1
  </update>
</mapper>
"""


def test_mybatis_statements_and_links():
    ns, stmts = mybatis.statements("m/EventMapper.xml", MAPPER_XML)
    assert ns == "com.x.repo.EventMapper"
    by = {s["name"]: s for s in stmts}
    assert by["getEventList"]["start_line"] == 4 and by["getEventList"]["end_line"] == 7
    assert by["updateEvent"]["label"] == "SqlStatement" and by["cols"]["label"] == "SqlFragment"
    methods = [{"qualified_name": "p.src.com.x.repo.EventMapper.getEventList", "name": "getEventList", "path": "EventMapper.java", "start_line": 10}]
    edges = mybatis.link_edges(ns, stmts, methods)
    assert len(edges) == 1 and edges[0]["strategy"] == "mybatis_namespace"


def test_yaml_nested_secret_keys_are_masked():
    lines = ["crypto:", "  secret:", "    key: plainsecretvalue", "app:", "  license-key: ABCD-1234", "  name: event-api",
             "spring:", "  datasource:", "    password: ENC(abc)"]
    out = mask_lines("application.yml", lines)
    assert out[2] == ("    key: ****", True)
    assert out[4][1] is True and "ABCD" not in out[4][0]
    assert out[5] == ("  name: event-api", False)
    assert out[8][1] is False  # 이미 암호화된 값은 그대로


def test_unit_level_uses_important_claims():
    claims = [{"level": 0, "severity": "info"}, {"level": 1, "severity": "important"}]
    assert loop.unit_level(claims) == 1
    assert loop.unit_level([{"level": 0, "severity": "info"}, {"level": 1, "severity": "info"}]) == 1
    assert loop.unit_level([]) == 0


def test_expired_lease_still_usable_by_owner_until_taken(opened, settings):
    conn, proj, _ = opened
    unit_id = loop.next_unit(conn, settings, "a1", kind="code")["unit"]["id"]
    conn.execute("UPDATE unit SET lease_expires_at = 0 WHERE id = ?", (unit_id,))
    loop.renew(conn, unit_id, "a1")  # 아무도 안 가져갔으면 원래 소유자가 계속 쓴다
    conn.execute("UPDATE unit SET lease_expires_at = 0 WHERE id = ?", (unit_id,))
    loop.claim_unit(conn, settings, "a2", unit_id)
    with pytest.raises(ToolError) as e:
        loop.renew(conn, unit_id, "a1")
    assert e.value.code == "lease_conflict"


def test_cli_bad_json_returns_envelope(tmp_path, capsys, monkeypatch, opened, settings):
    conn, proj, _ = opened
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    bad = tmp_path / "bad.json"
    bad.write_text('{"text": "a\\x"}', encoding="utf-8")
    code = cli.main(["unknown-add", proj["id"], "--agent", "t", "--json", str(bad)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["ok"] is False and out["errors"][0]["code"] == "invalid_request"
    lst = tmp_path / "list.json"
    lst.write_text(json.dumps([{"question": "q1", "domain": "d"}, {"question": "q2", "domain": "d"}]), encoding="utf-8")
    assert cli.main(["unknown-add", proj["id"], "--agent", "t", "--json", str(lst)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["created"] for r in out["data"]["results"]] == [True, True]
