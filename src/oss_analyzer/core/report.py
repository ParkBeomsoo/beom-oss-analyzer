"""리포트 — 2축(파일·심볼) 커버리지, 기능 카탈로그, 내보내기(Markdown + JSON)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from oss_analyzer.config import Settings
from oss_analyzer.core import ledger
from oss_analyzer.core.levels import LEVEL_NAMES
from oss_analyzer.core.project import workspace


def coverage(conn) -> dict:
    by_cat = {r["category"]: r["n"] for r in conn.execute("SELECT category, COUNT(*) n FROM file GROUP BY category")}
    unclassified = [r["path"] for r in conn.execute("SELECT path FROM file WHERE category = 'unclassified' ORDER BY path LIMIT 50")]
    units = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM unit GROUP BY status")}
    levels = {LEVEL_NAMES.get(r["level"], "미완료"): r["n"]
              for r in conn.execute("SELECT level, COUNT(*) n FROM unit WHERE status = 'done' GROUP BY level")}
    sym_total = conn.execute("SELECT COUNT(*) FROM symbol").fetchone()[0]
    sym_reviewed = conn.execute("SELECT COUNT(*) FROM symbol WHERE reviewed = 1").fetchone()[0]
    sym_unassigned = conn.execute("SELECT COUNT(*) FROM symbol WHERE unit_id IS NULL").fetchone()[0]
    open_unknowns = {r["severity"]: r["n"] for r in conn.execute(
        "SELECT severity, COUNT(*) n FROM unknown WHERE status != 'resolved' GROUP BY severity")}
    pending_reviews = conn.execute("SELECT COUNT(*) FROM claim WHERE review_status = 'pending'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM claim WHERE review_status = 'rejected'").fetchone()[0]
    unit_total = sum(units.values())

    gates = {
        "모든 파일 분류됨": by_cat.get("unclassified", 0) == 0,
        "모든 단위 완료": unit_total > 0 and units.get("done", 0) == unit_total,
        "모든 심볼 검토됨": sym_total == sym_reviewed,
        "단위 밖 심볼 없음": sym_unassigned == 0,
        "중요 결론 독립 검토 완료": pending_reviews == 0,
        "critical 미확인 없음": open_unknowns.get("critical", 0) == 0,
    }
    return {
        "files": {"by_category": by_cat, "unclassified_examples": unclassified},
        "units": {"by_status": units, "total": unit_total, "done_by_level": levels},
        "symbols": {"total": sym_total, "reviewed": sym_reviewed, "unassigned": sym_unassigned},
        "unknowns_open": open_unknowns,
        "claims": {"pending_review": pending_reviews, "rejected": rejected},
        "gates": gates,
        "complete": all(gates.values()),
    }


def features(conn, offset: int = 0, limit: int = 200) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM feature").fetchone()[0]
    rows = ledger.rows_to_dicts(conn.execute(
        "SELECT * FROM feature ORDER BY kind, path, start_line LIMIT ? OFFSET ?", (limit, offset)).fetchall())
    by_kind: dict[str, int] = defaultdict(int)
    for r in conn.execute("SELECT kind, COUNT(*) n FROM feature GROUP BY kind"):
        by_kind[r["kind"]] = r["n"]
    return {"total": total, "by_kind": dict(by_kind), "items": rows,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None}


def export(conn, settings: Settings, project: dict) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = workspace(settings, project["id"]) / "reports" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cov = coverage(conn)
    feats = ledger.rows_to_dicts(conn.execute("SELECT * FROM feature ORDER BY kind, path, start_line").fetchall())
    units = ledger.rows_to_dicts(conn.execute("SELECT * FROM unit ORDER BY kind, title").fetchall())
    claims = ledger.rows_to_dicts(conn.execute(
        "SELECT * FROM claim WHERE review_status != 'withdrawn' ORDER BY unit_id, created_at").fetchall())
    unknown_rows = ledger.rows_to_dicts(conn.execute(
        "SELECT * FROM unknown WHERE status != 'resolved' ORDER BY severity, domain").fetchall())
    ev_loc = {r["id"]: json.loads(r["locations"]) for r in conn.execute("SELECT id, locations FROM evidence").fetchall()}

    data = {"project": project, "coverage": cov, "features": feats, "units": units, "claims": claims, "unknowns": unknown_rows}
    (out_dir / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def loc_text(ev_ids: list[str]) -> str:
        parts = []
        for e in ev_ids:
            for loc in ev_loc.get(e, [])[:3]:
                parts.append(f"`{loc['path']}:{loc['start_line']}`" + (f"-{loc['end_line']}" if loc["end_line"] != loc["start_line"] else ""))
        return ", ".join(parts[:6])

    md = [f"# 분석 리포트 — {project['id']}", "",
          f"- 원본: `{project['source']}`", f"- 커밋: `{project['commit_sha']}`", f"- 생성: {stamp}", "",
          "## 완료 조건", ""]
    md += [f"- {'✅' if ok else '❌'} {name}" for name, ok in cov["gates"].items()]
    md += ["", "## 커버리지", "",
           f"- 파일 범주: {cov['files']['by_category']}",
           f"- 단위: {cov['units']['by_status']} / 등급: {cov['units']['done_by_level']}",
           f"- 심볼: {cov['symbols']['reviewed']}/{cov['symbols']['total']} 검토 (단위 밖 {cov['symbols']['unassigned']})",
           f"- 열린 미확인: {cov['unknowns_open']}", "", "## 기능 카탈로그 (진입점)", "",
           "| 종류 | 메서드 | 경로·이름 | 프레임워크 | 위치 | 핸들러 | 비고 |", "|---|---|---|---|---|---|---|"]
    for f in feats:
        if f["kind"].endswith("-template"):
            continue  # 동적 등록 지점 자체는 펼친 경로들로 대체
        name = f.get("full_path") or f["name"]
        notes = [a for a in (f.get("assumptions") or [])][:2] + (["테스트 코드"] if f.get("in_test") else [])
        md.append(f"| {f['kind']} | {f.get('method') or ''} | `{name[:90]}` | {f['framework']} | `{f['path']}:{f['start_line']}` "
                  f"| {(f['handler'] or '')[:50]} | {'; '.join(notes)[:120]} |")
    md += ["", "## 단위별 결론", ""]
    claims_by_unit: dict[str, list[dict]] = defaultdict(list)
    for c in claims:
        claims_by_unit[c["unit_id"]].append(c)
    for u in units:
        if u["status"] != "done":
            continue
        md += [f"### {u['title']} ({LEVEL_NAMES.get(u['level'], '?')})", "", u["summary"] or "", ""]
        for c in claims_by_unit.get(u["id"], []):
            mark = {"confirmed": "✔", "rejected": "✖", "pending": "⏳"}.get(c["review_status"], "")
            md.append(f"- [{LEVEL_NAMES[c['level']]}]{mark} {c['text']} — {loc_text(c['evidence_ids'])}")
        md.append("")
    md += ["## 열린 미확인", "", "| 심각도 | 영역 | 질문 | 요구 등급 |", "|---|---|---|---|"]
    for q in unknown_rows:
        md.append(f"| {q['severity']} | {q['domain']} | {q['question'][:160]} | {LEVEL_NAMES[q['required_level']]} |")
    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"dir": str(out_dir), "markdown": str(out_dir / "report.md"), "json": str(out_dir / "report.json"),
            "complete": cov["complete"]}
