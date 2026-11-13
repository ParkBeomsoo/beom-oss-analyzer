"""요청사항 기반 만들기 — 원본 없이 "이런 기능을 만들어 줘" 에서 시작한다.

포팅 원장을 그대로 쓰되, 옮길 항목 대신 요구사항(R1, R2 …)이 항목이 된다.
- 요구사항 목록과 합격 기준(케이스의 expect)은 구현 전에 사람이 승인한다 — 목표가 고정된다.
- 케이스는 모두 원본이 없는 new 케이스이고, 각 케이스는 req 로 어느 요구사항을 확인하는지 밝힌다.
- 입력 변형 케이스는 기대값 대신 불변 조건(죽지 않음, 5xx 아님)으로 판정한다.
- 요구사항마다 구현 위치(경로:줄)를 매핑하고, 그 코드가 케이스로 실행됐는지 대상 커버리지로 본다.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import ledger
from oss_analyzer.core import port_guard as guard
from oss_analyzer.core import project as project_mod

MODE = "request"
REQ_ID = re.compile(r"R\d+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(settings: Settings, *, name: str, request: str, target_root: str, context: str | None = None,
           author: str = "agent") -> dict:
    """요청사항 프로젝트(원본 없는 원장)와 그 안의 만들기 원장을 만든다. 같은 이름·대상이면 다시 쓴다.
    context: 함께 볼 분석 프로젝트 id (넣을 저장소나 참고 저장소를 open·inventory 한 것)."""
    if not request or not request.strip():
        raise ToolError("invalid_request", "요청사항(request)이 비어 있음")
    root = Path(target_root).resolve()
    key = hashlib.sha256(f"{name}|{root}".encode()).hexdigest()
    pid = f"req-{project_mod._slug(name)}-{key[:8]}"
    ws = project_mod.workspace(settings, pid)
    if (ws / "ledger.db").exists():
        conn, proj = project_mod.load(settings, pid)
    else:
        ws.mkdir(parents=True, exist_ok=True)
        conn = project_mod.connection_create(settings, pid)
        conn.execute("INSERT INTO project (id, source, commit_sha, repo_dir, created_at) VALUES (?, ?, ?, ?, ?)",
                     (pid, f"request:{name}", MODE, str(root), _now()))
        conn.commit()
        proj = project_mod.load(settings, pid)[1]
    port_id = "port_" + key[8:20]
    with ledger.tx(conn):
        conn.execute("INSERT INTO port (id, name, targets, target_language, target_root, slice, author, created_at, mode, request, context) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET request = excluded.request, "
                     "context = excluded.context, name = excluded.name",
                     (port_id, name, ledger.dumps([]), "", str(root), ledger.dumps({"symbols": [], "entries": []}), author, _now(),
                      MODE, request.strip(), context))
    guard.record(conn, port_id, "create", {"mode": MODE, "author": author})
    return {"project_id": pid, "port_id": port_id, "mode": MODE, "target_root": str(root),
            "next_step": "make-req 로 요구사항 목록 작성 → 요구사항마다 합격 케이스(expect + req) 작성 → port-capture"}


def is_request(port: dict) -> bool:
    return port.get("mode") == MODE


def set_requirements(conn, port_id: str, reqs: list[dict], actor: str = "agent") -> dict:
    """요구사항 목록을 통째로 정한다. [{id?: "R1", text: "..."}]. 없는 id 는 순서대로 붙인다.
    문장이 바뀐 요구사항은 다시 pending (구현 위치를 다시 확인), 목록에서 빠진 것은 지운다.
    목록이 바뀌면 지문이 달라져 사용자 승인을 다시 받아야 한다."""
    port = ledger.row_to_dict(conn.execute("SELECT * FROM port WHERE id = ?", (port_id,)).fetchone())
    if port is None or not is_request(port):
        raise ToolError("invalid_request", "요청사항 모드 원장이 아님", recovery="make-create 로 만든 port_id 를 쓴다")
    if not reqs:
        raise ToolError("invalid_request", "요구사항이 비어 있음")
    existing = {r["item_id"]: r for r in ledger.rows_to_dicts(conn.execute(
        "SELECT * FROM port_map WHERE port_id = ? AND kind = 'requirement'", (port_id,)).fetchall())}
    used = {r.get("id") for r in reqs if r.get("id")}
    bad = [i for i in used if not REQ_ID.fullmatch(str(i))]
    if bad:
        raise ToolError("invalid_request", f"요구사항 id 는 R1, R2 … 형식: {bad}")
    n = max([int(i[1:]) for i in list(used) + list(existing)] + [0])
    keep, out = set(), []
    with ledger.tx(conn):
        for r in reqs:
            text = (r.get("text") or "").strip()
            if not text:
                raise ToolError("invalid_request", f"요구사항 문장이 비어 있음: {r}")
            rid = r.get("id")
            if not rid:
                n += 1
                rid = f"R{n}"
            keep.add(rid)
            old = existing.get(rid)
            if old is None:
                conn.execute("INSERT INTO port_map (port_id, item_id, kind, label, status, actor, updated_at) "
                             "VALUES (?, ?, 'requirement', ?, 'pending', ?, ?)", (port_id, rid, text, actor, _now()))
                out.append({"id": rid, "change": "added"})
            elif old["label"] != text:
                conn.execute("UPDATE port_map SET label = ?, status = 'pending', target_ref = NULL, target_text = NULL, note = NULL, "
                             "actor = ?, updated_at = ? WHERE port_id = ? AND item_id = ?", (text, actor, _now(), port_id, rid))
                out.append({"id": rid, "change": "changed"})
        for rid in set(existing) - keep:
            conn.execute("DELETE FROM port_map WHERE port_id = ? AND item_id = ?", (port_id, rid))
            out.append({"id": rid, "change": "removed"})
    guard.record(conn, port_id, "requirements", {"ids": sorted(keep), "actor": actor})
    return {"requirements": requirements(conn, port_id), "changes": out,
            "note": "요구사항 목록은 사용자 승인 대상 — 바뀌면 다시 승인해야 한다"}


def requirements(conn, port_id: str) -> list[dict]:
    rows = ledger.rows_to_dicts(conn.execute(
        "SELECT item_id, label, status, target_ref, note FROM port_map WHERE port_id = ? AND kind = 'requirement'",
        (port_id,)).fetchall())
    return sorted(({"id": r["item_id"], "text": r["label"], "status": r["status"], "target": r["target_ref"], "note": r["note"]}
                   for r in rows), key=lambda r: int(r["id"][1:]))


def prepare_cases(cases: list[dict]) -> list[dict]:
    """요청사항 모드 케이스 확인: 모두 expect 가 있어야 하고 req 로 요구사항을 밝힌다. 원본이 없으므로 new 로 둔다."""
    out = []
    for i, c in enumerate(cases):
        name = c.get("name") or f"#{i + 1}"
        if not c.get("expect"):
            raise ToolError("invalid_request", f"케이스 {name}: 요청사항 모드에서는 expect(기대값)가 필요함",
                            recovery="요구사항에서 정한 결과를 expect 로 적는다 — 대상 코드의 출력을 베껴 쓰지 않는다")
        reqs = c.get("req")
        if isinstance(reqs, str):
            reqs = [reqs]
        if not reqs or not all(REQ_ID.fullmatch(str(r)) for r in reqs):
            raise ToolError("invalid_request", f"케이스 {name}: req 에 확인하는 요구사항 id(R1 …)가 필요함")
        out.append({**c, "req": list(reqs), "new": True})
    return out


def invariant_variants(cases: list[dict], generated: list[dict]) -> list[dict]:
    """입력 변형 케이스: 기대값은 그 입력에 대한 것이라 떼고, 불변 조건으로만 판정한다."""
    return [{**{k: v for k, v in g.items() if k not in ("expect", "req")}, "new": True, "invariant": True} for g in generated]


def coverage_of(cases: list[dict], req_ids: list[str]) -> dict[str, list[str]]:
    cover = {r: [] for r in req_ids}
    for c in cases:
        if c.get("generated"):
            continue
        for r in c.get("req") or []:
            if r in cover:
                cover[r].append(c.get("name"))
    return cover
