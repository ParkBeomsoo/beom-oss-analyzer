"""미확인(Unknown) — REA 의 ResidualUnknown 구조를 재구현하고 '요구 등급 강제'를 추가.

- 상태: open / investigating / blocked / contradicted / resolved
- 해결(resolved)은 required_level 이상인 근거가 1개 이상 있어야만 가능 (REA 는 미강제)
- 'withdrawn' / 'out-of-scope' 로 닫을 때도 사유가 필수
- 갱신은 expected_revision 으로 낙관적 잠금 → 동시에 고치면 revision_conflict
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import evidence as evidence_mod
from oss_analyzer.core import ledger
from oss_analyzer.core.levels import LEVEL_NAMES, parse_level

STATUSES = {"open", "investigating", "blocked", "contradicted", "resolved"}
SEVERITIES = {"low", "medium", "high", "critical"}
DISPOSITIONS = {"verified", "withdrawn", "out-of-scope"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unknown_id(domain: str, question: str, scope: str | None) -> str:
    normalized = " ".join(question.split()).lower()
    return "unk_" + hashlib.sha256(f"{domain}|{normalized}|{scope or ''}".encode()).hexdigest()[:20]


def record(
    conn, *, question: str, domain: str, scope: str | None = None, severity: str = "medium",
    required_level: int | str = 1, supporting_evidence_ids: list[str] | None = None,
    recommended_probes: list[dict] | None = None, origin: str = "agent",
) -> dict:
    if severity not in SEVERITIES:
        raise ToolError("invalid_request", f"severity 는 {sorted(SEVERITIES)} 중 하나")
    level = parse_level(required_level)
    for ev_id in supporting_evidence_ids or []:
        evidence_mod.get(conn, ev_id)
    uid = unknown_id(domain, question, scope)
    existing = conn.execute("SELECT id, status, revision FROM unknown WHERE id = ?", (uid,)).fetchone()
    if existing:
        return {"unknown_id": uid, "created": False, "reason": "같은 영역·질문·범위의 미확인이 이미 있음",
                "status": existing["status"], "revision": existing["revision"]}
    conn.execute(
        "INSERT INTO unknown (id, revision, question, domain, scope, status, severity, required_level, "
        "supporting_evidence_ids, contradicting_evidence_ids, recommended_probes, resolution, origin, history, updated_at) "
        "VALUES (?, 1, ?, ?, ?, 'open', ?, ?, ?, '[]', ?, NULL, ?, ?, ?)",
        (
            uid, question, domain, scope, severity, level, ledger.dumps(supporting_evidence_ids or []),
            ledger.dumps(recommended_probes or []), origin,
            ledger.dumps([{"revision": 1, "status": "open", "at": _now(), "by": origin}]), _now(),
        ),
    )
    return {"unknown_id": uid, "created": True}


def update(
    conn, unknown_id_: str, *, expected_revision: int, actor: str, status: str | None = None,
    add_supporting: list[str] | None = None, add_contradicting: list[str] | None = None,
    resolution: dict | None = None, note: str | None = None,
) -> dict:
    row = ledger.row_to_dict(conn.execute("SELECT * FROM unknown WHERE id = ?", (unknown_id_,)).fetchone())
    if row is None:
        raise ToolError("not_found", f"미확인 없음: {unknown_id_}")
    if row["revision"] != expected_revision:
        raise ToolError(
            "revision_conflict", f"revision 이 {row['revision']} 로 바뀜 (요청 {expected_revision})",
            recovery="oss_unknowns_list 로 다시 읽고 재시도", data={"current_revision": row["revision"]},
        )
    supporting = list(dict.fromkeys(row["supporting_evidence_ids"] + (add_supporting or [])))
    contradicting = list(dict.fromkeys(row["contradicting_evidence_ids"] + (add_contradicting or [])))
    for ev_id in (add_supporting or []) + (add_contradicting or []):
        evidence_mod.get(conn, ev_id)
    if set(supporting) & set(contradicting):
        raise ToolError("invalid_request", "같은 근거를 지지·반박에 동시에 넣을 수 없음")

    new_status = "resolved" if resolution is not None else (status or row["status"])
    if new_status not in STATUSES:
        raise ToolError("invalid_request", f"status 는 {sorted(STATUSES)} 중 하나")
    if new_status == "contradicted" and not contradicting:
        raise ToolError("invalid_request", "contradicted 는 반박 근거가 필요함")
    if new_status == "resolved" and resolution is None and row["resolution"] is None:
        raise ToolError("invalid_request", "resolved 로 바꾸려면 resolution 이 필요함")
    # 다시 열면(resolved → 다른 상태) 기존 해결 내용은 지운다
    final_resolution = resolution if resolution is not None else (row["resolution"] if new_status == "resolved" else None)

    if resolution is not None:
        disposition = resolution.get("disposition")
        if disposition not in DISPOSITIONS:
            raise ToolError("invalid_request", f"disposition 은 {sorted(DISPOSITIONS)} 중 하나")
        if not resolution.get("rationale"):
            raise ToolError("invalid_request", "resolution.rationale 이 필요함")
        ev_ids = resolution.get("evidence_ids") or []
        if disposition == "verified":
            levels = [evidence_mod.get(conn, e)["level"] for e in ev_ids]
            if not levels or max(levels) < row["required_level"]:
                raise ToolError(
                    "gate_failed",
                    f"이 미확인은 {LEVEL_NAMES[row['required_level']]} 이상 근거가 있어야 해결 가능 "
                    f"(제출 근거 최고 {LEVEL_NAMES[max(levels)] if levels else '없음'})",
                    recovery="더 높은 등급 도구(LSP·데이터 흐름·실행 검증)로 근거를 만든 뒤 다시 시도",
                )

    history = row["history"] + [{
        "revision": row["revision"] + 1, "status": new_status, "at": _now(), "by": actor, "note": note,
    }]
    conn.execute(
        "UPDATE unknown SET revision = revision + 1, status = ?, supporting_evidence_ids = ?, "
        "contradicting_evidence_ids = ?, resolution = ?, history = ?, updated_at = ? WHERE id = ? AND revision = ?",
        (
            new_status, ledger.dumps(supporting), ledger.dumps(contradicting),
            ledger.dumps(final_resolution) if final_resolution is not None else None,
            ledger.dumps(history), _now(), unknown_id_, expected_revision,
        ),
    )
    return {"unknown_id": unknown_id_, "revision": row["revision"] + 1, "status": new_status}


def list_(conn, *, status: str | None = None, severity: str | None = None, domain: str | None = None,
          offset: int = 0, limit: int = 50) -> dict:
    where, params = [], []
    for col, val in (("status", status), ("severity", severity), ("domain", domain)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM unknown {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM unknown {clause} ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        f"WHEN 'medium' THEN 2 ELSE 3 END, id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    items = ledger.rows_to_dicts(rows)
    for item in items:
        item.pop("history", None)
    next_offset = offset + len(items) if offset + len(items) < total else None
    return {"total": total, "items": items, "next_offset": next_offset}
