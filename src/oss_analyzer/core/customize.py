"""커스텀 작업 흐름 — 영향도 분석 · 커스텀 원장 · 업스트림 버전 비교.

영향도는 세 갈래로 모은다 (갈래마다 등급이 다르고, 결과에 그대로 표시):
  call       구조 지도 호출 간선 (L0) — verify=True 면 LSP incoming 으로 대조해 L1 근거를 남기고 누락 간선을 보탠다
  interface  인터페이스 메서드 ↔ 구현 메서드 (확장 포인트 표, L0) — 동적 디스패치라 호출 간선에 안 잡히는 경로
  name       이름 일치 호출 텍스트 (L0, 낮은 신뢰) — 위 두 갈래가 놓친 후보. 결론 근거로 쓰기 전 LSP 확인 필수

커스텀 원장은 대상 심볼의 시그니처·본문 해시를 앵커로 저장해 두고, 업스트림 새 버전과 비교할 때
앵커가 사라졌는지/바뀌었는지/이름이 바뀌었는지로 충돌 지점을 계산한다.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import evidence, ledger
from oss_analyzer.core import project as project_mod

KINDS = {"extend", "modify", "replace", "config", "remove"}
STATUSES = {"planned", "applied", "dropped"}
MAX_NAME_HITS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 심볼 해석

def local_name(project_id: str, qualified: str) -> str:
    """구조 지도 정규 이름에서 프로젝트 접두를 뗀다 (버전이 달라도 같은 심볼이면 같은 이름)."""
    if qualified.startswith(project_id + "."):
        return qualified[len(project_id) + 1:]
    return qualified.split(".", 1)[1] if "." in qualified else qualified


def resolve_targets(conn, targets: list[str]) -> tuple[list[dict], list[str]]:
    """대상 → 심볼. 형식: 심볼 id(s_…) / 경로:줄 / 파일 경로 / 이름 또는 정규 이름 꼬리 (pricing.calc_total)."""
    found: dict[str, dict] = {}
    notes: list[str] = []
    for t in targets:
        t = t.strip().replace("\\", "/")
        rows: list = []
        if t.startswith("s_"):
            rows = conn.execute("SELECT * FROM symbol WHERE id = ?", (t,)).fetchall()
        elif re.fullmatch(r".+:\d+", t):
            path, line = t.rsplit(":", 1)
            rows = conn.execute("SELECT * FROM symbol WHERE path = ? AND start_line <= ? AND end_line >= ? "
                                "ORDER BY (end_line - start_line) LIMIT 1", (path, int(line), int(line))).fetchall()
        elif conn.execute("SELECT 1 FROM file WHERE path = ?", (t,)).fetchone():
            rows = conn.execute("SELECT * FROM symbol WHERE path = ?", (t,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM symbol WHERE qualified_name = ? OR qualified_name LIKE ? OR name = ?",
                                (t, f"%.{t}", t)).fetchall()
            if len(rows) > 1:
                notes.append(f"'{t}' 가 심볼 {len(rows)}개에 해당 — 모두 포함 (좁히려면 심볼 id 나 경로:줄 사용)")
        if not rows:
            notes.append(f"'{t}' 에 해당하는 심볼 없음")
        for r in rows:
            found[r["id"]] = dict(r)
    return list(found.values()), notes


def _symbols(conn) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    by_qn, by_path = {}, defaultdict(list)
    for r in conn.execute("SELECT * FROM symbol").fetchall():
        d = dict(r)
        by_qn[d["qualified_name"]] = d
        by_path[d["path"]].append(d)
    return by_qn, by_path


def _enclosing(by_path: dict[str, list[dict]], path: str, line: int) -> dict | None:
    best = None
    for s in by_path.get(path, []):
        if s["start_line"] <= line <= s["end_line"]:
            if best is None or (s["end_line"] - s["start_line"]) < (best["end_line"] - best["start_line"]):
                best = s
    return best


def feature_symbol(by_path: dict[str, list[dict]], f: dict) -> dict | None:
    """진입점 → 처리 함수 심볼. handler 이름 → 바로 아래 정의(데코레이터·어노테이션) → 감싸는 심볼."""
    cands = by_path.get(f["path"], [])
    if f.get("handler"):
        named = [s for s in cands if s["name"] == f["handler"]]
        if named:
            return named[0]
        parts = [p for p in re.split(r"::|\.", f["handler"]) if p and p not in ("self", "super", "crate")]
        if len(parts) >= 2:
            name, mods = parts[-1], parts[:-1]
            here = str(PurePosixPath(f["path"]).parent)
            # 라우트 파일 옆의 모듈 파일(<mod>.rs·<mod>/mod.rs·<mod>.py·<mod>/index.ts …) 안의 같은 이름 정의
            def in_module(path: str) -> bool:
                p = PurePosixPath(path)
                rel = str(p.parent) if p.stem in ("mod", "index", "__init__") else str(p.with_suffix(""))
                return rel.endswith("/".join(mods)) and (rel.startswith(here) or here == ".")
            hits = [s for path, ss in by_path.items() if in_module(path) for s in ss if s["name"] == name]
            if len(hits) == 1:
                return hits[0]
    below = [s for s in cands if f["start_line"] <= s["start_line"] <= f["end_line"] + 4]
    if below:
        return min(below, key=lambda s: s["start_line"])
    return _enclosing(by_path, f["path"], f["start_line"])


def _name_column(lines: list[str], s: dict) -> tuple[int, int] | None:
    for ln in range(s["start_line"], min(s["start_line"] + 4, len(lines) + 1)):
        m = re.search(rf"\b{re.escape(s['name'])}\b", lines[ln - 1])
        if m:
            return ln, m.start() + 1
    return None


# ---------------------------------------------------------------- 영향도

def impact(conn, proj: dict, targets: list[str], depth: int = 4, verify: bool = False, pool=None,
           name_match: bool = True) -> dict:
    seeds, notes = resolve_targets(conn, targets)
    if not seeds:
        raise ToolError("not_found", "대상 심볼을 찾지 못함", recovery="; ".join(notes) or None)
    by_qn, by_path = _symbols(conn)
    by_id = {s["id"]: s for s in by_qn.values()}
    categories = {r["path"]: r["category"] for r in conn.execute("SELECT path, category FROM file").fetchall()}
    callers_of: dict[str, list[dict]] = defaultdict(list)
    for e in conn.execute("SELECT * FROM call_edge WHERE callee NOT LIKE '\\_\\_route\\_\\_%' ESCAPE '\\'").fetchall():
        callers_of[e["callee"]].append(dict(e))

    # 인터페이스 메서드 ↔ 구현 메서드
    impl_links: dict[str, list[tuple[str, str]]] = defaultdict(list)  # 심볼 id → [(관련 심볼 id, 관계)]
    for xp in ledger.rows_to_dicts(conn.execute("SELECT * FROM extension_point WHERE kind = 'interface'").fetchall()):
        iface = by_path_class(by_path, xp["path"], xp["name"])
        if iface is None:
            continue
        for impl in xp["implementations"]:
            cls = by_path_class(by_path, impl["path"], impl["name"])
            if cls is None:
                continue
            for im in _methods(by_path, iface):
                for cm in _methods(by_path, cls):
                    if cm["name"] == im["name"]:
                        impl_links[im["id"]].append((cm["id"], "implementation"))
                        impl_links[cm["id"]].append((im["id"], "interface-method"))

    impacted: dict[str, dict] = {}
    module_callers: list[dict] = []

    def expand(start: list[str]) -> None:
        q: deque = deque(start)
        while q:
            sid = q.popleft()
            cur = impacted[sid]
            if cur["depth"] >= depth:
                continue
            sym = by_id[sid]
            nxt: list[tuple[str, str, int | None]] = []
            for e in callers_of.get(sym["qualified_name"], []):
                caller = by_qn.get(e["caller"])
                if caller is None:
                    module_callers.append({"caller": e["caller"], "path": e["caller_path"], "line": e["call_line"], "callee": sym["id"]})
                    continue
                nxt.append((caller["id"], "call", e["call_line"]))
            for rel_id, rel in impl_links.get(sid, []):
                nxt.append((rel_id, rel, None))
            for nid, via, line in nxt:
                if nid in impacted:
                    continue
                impacted[nid] = {"symbol": by_id[nid], "depth": cur["depth"] + 1, "via": via, "level": "L0",
                                 "from": sid, "line": line, "possible": cur.get("possible", False)}
                q.append(nid)

    for s in seeds:
        impacted[s["id"]] = {"symbol": s, "depth": 0, "via": "target", "level": "L0", "from": None}
    expand([s["id"] for s in seeds])

    # LSP 대조 — 구조 지도가 놓친 호출자를 L1 로 보탠다
    lsp_out = []
    if verify and pool is not None:
        from oss_analyzer.core import engines

        for s in seeds[:10]:
            try:
                lines = evidence.read_lines(proj, s["path"])
                pos = _name_column(lines, s)
                if pos is None:
                    continue
                op = "incoming"
                try:
                    res = engines.lsp_call_hierarchy(conn, pool, proj, s["path"], pos[0], pos[1], "incoming")
                except ToolError as e:
                    if e.code != "capability_unavailable":
                        raise
                    op = "references"  # callHierarchy 미지원 서버 — 참조로 대신 (import·타입 표기도 섞임)
                    res = engines.lsp_references(conn, pool, proj, s["path"], pos[0], pos[1])
            except ToolError as e:
                lsp_out.append({"symbol_id": s["id"], "ok": False, "error": e.message})
                continue
            except Exception as e:  # noqa: BLE001 — 언어 서버 오류는 대상 단위로 격리
                lsp_out.append({"symbol_id": s["id"], "ok": False, "error": f"{type(e).__name__}: {e}"})
                continue
            added = 0
            for loc in res.get("locations", []):
                caller = _enclosing(by_path, loc["path"], loc["start_line"])
                if caller is None or caller["id"] == s["id"]:
                    continue
                if caller["id"] in impacted:
                    impacted[caller["id"]]["level"] = "L1"
                else:
                    impacted[caller["id"]] = {"symbol": caller, "depth": 1, "via": "call", "level": "L1", "from": s["id"],
                                              "line": loc["start_line"], "missed_by_graph": True}
                    added += 1
            lsp_out.append({"symbol_id": s["id"], "ok": True, "operation": op, "evidence_id": res.get("evidence_id"),
                            "count": res.get("count", 0), "missed_by_graph": added})

    # 이름 일치 후보 — 동적 호출(obj.method(), 콜백)처럼 간선이 없는 경로
    possible = []
    if name_match:
        # 대상 + 인터페이스로 연결된 메서드: 동적 디스패치(obj.method())는 호출 간선이 없어서 이름으로만 잡힌다
        linked = seeds + [v["symbol"] for v in impacted.values() if v["via"] in ("interface-method", "implementation")]
        possible = _name_hits(conn, proj, linked, by_path, impacted, categories)
        start = []
        for p in possible:
            if p["symbol_id"] and p["symbol_id"] not in impacted:
                src = impacted[p["from"]]
                impacted[p["symbol_id"]] = {"symbol": by_id[p["symbol_id"]], "depth": src["depth"] + 1, "via": "name",
                                            "level": "L0", "from": p["from"], "line": p["line"], "possible": True}
                start.append(p["symbol_id"])
        expand(start)

    feats = []
    for f in ledger.rows_to_dicts(conn.execute("SELECT * FROM feature WHERE in_test = 0").fetchall()):
        fs = feature_symbol(by_path, f)
        if fs and fs["id"] in impacted:
            feats.append({"kind": f["kind"], "method": f["method"], "path": f["full_path"] or f["name"],
                          "location": f"{f['path']}:{f['start_line']}", "handler": fs["id"],
                          "via": impacted[fs["id"]]["via"], "level": impacted[fs["id"]]["level"],
                          **({"possible": True} if impacted[fs["id"]].get("possible") else {})})

    ids = set(impacted)
    tests = [_brief(v) for k, v in impacted.items() if categories.get(v["symbol"]["path"]) == "test"]
    claims = [c for c in ledger.rows_to_dicts(conn.execute("SELECT id, unit_id, text, severity, symbol_ids, level FROM claim "
                                                           "WHERE review_status != 'rejected'").fetchall())
              if ids & set(c["symbol_ids"])]
    customs = [c for c in ledger.rows_to_dicts(conn.execute("SELECT id, description, kind, status, symbol_ids FROM customization "
                                                            "WHERE status != 'dropped'").fetchall())
               if ids & set(c["symbol_ids"])]
    seed_ids = {s["id"] for s in seeds}
    xps = [x for x in ledger.rows_to_dicts(conn.execute("SELECT id, kind, name, path, line, symbol_id FROM extension_point").fetchall())
           if x["symbol_id"] in seed_ids or any(x["path"] == s["path"] for s in seeds)]

    gaps = []
    for s in seeds:
        has_callers = any(v["from"] == s["id"] for v in impacted.values()) or any(m["callee"] == s["id"] for m in module_callers)
        if not has_callers and not any(f["handler"] == s["id"] for f in feats) and categories.get(s["path"]) != "test":
            gaps.append({"symbol_id": s["id"], "name": s["name"],
                         "note": "호출자 0건 (구조 지도·인터페이스 기준) — 진입점·동적 호출·외부 사용일 수 있음. verify=True(LSP) 또는 이름 후보 확인"})

    return {
        "targets": [_brief_sym(s) for s in seeds], "notes": notes,
        "impacted": sorted((_brief(v) for v in impacted.values() if v["depth"] > 0), key=lambda x: (x["depth"], x["path"])),
        "module_level_callers": module_callers[:100],
        "possible_callers": [p for p in possible if p["category"] != "test"][:100],
        "features": feats, "tests": tests,
        "units": sorted({v["symbol"]["unit_id"] for v in impacted.values() if v["symbol"].get("unit_id")}),
        "claims_to_recheck": claims, "customizations": customs, "extension_points_nearby": xps,
        "gaps": gaps, "lsp": lsp_out,
        "levels": ("call/interface/name 은 L0(구조 지도·패턴). LSP 로 확인된 항목만 L1. "
                   "possible=true 는 이름 일치 후보에서 이어진 경로 — 근거가 아니라 확인할 목록"),
    }


def by_path_class(by_path: dict[str, list[dict]], path: str, name: str) -> dict | None:
    for s in by_path.get(path, []):
        if s["name"] == name and s["label"] in ("Class", "Interface", "Trait", "Struct", "Type"):
            return s
    for s in by_path.get(path, []):
        if s["name"] == name:
            return s
    return None


def _methods(by_path: dict[str, list[dict]], cls: dict) -> list[dict]:
    return [s for s in by_path.get(cls["path"], [])
            if s["id"] != cls["id"] and cls["start_line"] <= s["start_line"] <= cls["end_line"]
            and s["label"] in ("Method", "Function")]


def _name_hits(conn, proj, seeds, by_path, impacted, categories) -> list[dict]:
    out, seen = [], set(impacted)
    paths = [r["path"] for r in conn.execute("SELECT path FROM file WHERE category IN ('code', 'test')").fetchall()]
    for s in seeds:
        if len(s["name"]) < 3 or s["label"] in ("Class", "Module", "File"):
            continue
        pat = re.compile(rf"(?:\.|\b){re.escape(s['name'])}\s*\(")
        defn = re.compile(rf"\b(?:def|function|fn|func)\s+{re.escape(s['name'])}\b|^\s*(?:public|private|protected|static|\s)*[\w<>\[\]]+\s+{re.escape(s['name'])}\s*\(")
        for path in paths:
            try:
                lines = evidence.read_lines(proj, path)
            except (OSError, ToolError):
                continue
            for i, text in enumerate(lines, start=1):
                if not pat.search(text) or defn.search(text):
                    continue
                enc = _enclosing(by_path, path, i)
                key = enc["id"] if enc else f"{path}:{i}"
                if key in seen:
                    continue
                seen.add(key)
                out.append({"symbol_id": enc["id"] if enc else None, "name": enc["name"] if enc else None, "path": path,
                            "line": i, "category": categories.get(path), "matches": s["name"], "from": s["id"],
                            "level": "L0", "confidence": "name-match"})
                if len(out) >= MAX_NAME_HITS:
                    return out
    return out


def _brief_sym(s: dict) -> dict:
    return {"symbol_id": s["id"], "name": s["name"], "label": s["label"], "path": s["path"],
            "start_line": s["start_line"], "end_line": s["end_line"]}


def _brief(v: dict) -> dict:
    return {**_brief_sym(v["symbol"]), "depth": v["depth"], "via": v["via"], "level": v["level"], "from": v["from"],
            **({"missed_by_graph": True} if v.get("missed_by_graph") else {}),
            **({"possible": True} if v.get("possible") else {})}


# ---------------------------------------------------------------- 시그니처·본문 지문

def fingerprint(proj: dict, s: dict) -> dict:
    """심볼의 시그니처(정의 머리)와 본문 해시. 본문 해시는 이름을 지운 것도 같이 (이름 변경 탐지)."""
    try:
        lines = evidence.read_lines(proj, s["path"])
    except (OSError, ToolError):
        return {"signature": None, "body": None, "body_anon": None}
    body = lines[s["start_line"] - 1:s["end_line"]]
    head = []
    for ln in body[:6]:
        head.append(ln.strip())
        if re.search(r"[:{]\s*(?:#.*|//.*)?$|=>\s*\{?$|;\s*$|\)\s*$", ln.rstrip()) and "(" in " ".join(head):
            break
    signature = re.sub(r"\s+", " ", " ".join(head)).rstrip("{: ").strip()
    norm = "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in body if ln.strip())
    return {"signature": signature, "body": _sha(norm),
            "body_anon": _sha(re.sub(rf"\b{re.escape(s['name'])}\b", "§", norm))}


# ---------------------------------------------------------------- 커스텀 원장

def record(conn, proj: dict, *, description: str, targets: list[str], kind: str = "modify",
           extension_point_id: str | None = None, author: str = "agent") -> dict:
    if kind not in KINDS:
        raise ToolError("invalid_request", f"kind 는 {sorted(KINDS)} 중 하나")
    seeds, notes = resolve_targets(conn, targets) if targets else ([], [])
    if extension_point_id and not conn.execute("SELECT 1 FROM extension_point WHERE id = ?", (extension_point_id,)).fetchone():
        raise ToolError("not_found", f"확장 포인트 없음: {extension_point_id}")
    if not seeds and not extension_point_id:
        raise ToolError("invalid_request", "대상 심볼이나 확장 포인트가 하나는 있어야 함", recovery="; ".join(notes) or None)
    anchors = [{"symbol_id": s["id"], "name": local_name(proj["id"], s["qualified_name"]), "path": s["path"],
                "start_line": s["start_line"], "end_line": s["end_line"], **fingerprint(proj, s)} for s in seeds]
    cid = "cus_" + _sha(f"{description}|{sorted(s['id'] for s in seeds)}|{extension_point_id}")
    units = sorted({s["unit_id"] for s in seeds if s.get("unit_id")})
    with ledger.tx(conn):
        conn.execute(
            "INSERT OR REPLACE INTO customization (id, description, symbol_ids, unit_ids, created_at, kind, status, author, "
            "extension_point_id, anchors, commit_sha, history) VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?)",
            (cid, description, ledger.dumps([s["id"] for s in seeds]), ledger.dumps(units), _now(), kind, author,
             extension_point_id, ledger.dumps(anchors), proj["commit_sha"],
             ledger.dumps([{"status": "planned", "at": _now(), "by": author}])))
    out: dict = {"customization_id": cid, "anchors": len(anchors), "units": units, "notes": notes}
    if seeds:
        imp = impact(conn, proj, [s["id"] for s in seeds], depth=3)
        out["impact"] = {"impacted": len(imp["impacted"]), "possible_callers": len(imp["possible_callers"]),
                         "features": imp["features"], "tests": [t["path"] + ":" + str(t.get("start_line") or t.get("line"))
                                                                for t in imp["tests"]],
                         "gaps": imp["gaps"]}
        if kind in ("modify", "replace") and imp["extension_points_nearby"]:
            out["consider_instead"] = [{"extension_point_id": x["id"], "kind": x["kind"], "name": x["name"],
                                        "at": f"{x['path']}:{x['line']}"} for x in imp["extension_points_nearby"]]
            out["note"] = "코어 수정 대신 확장 포인트로 같은 목적을 이룰 수 있는지 먼저 검토 (업스트림 갱신 시 충돌 감소)"
    return out


def list_customizations(conn, status: str | None = None) -> dict:
    sql = "SELECT * FROM customization" + (" WHERE status = ?" if status else "") + " ORDER BY created_at"
    return {"items": ledger.rows_to_dicts(conn.execute(sql, (status,) if status else ()).fetchall())}


def update_customization(conn, cid: str, *, status: str, note: str, actor: str) -> dict:
    if status not in STATUSES:
        raise ToolError("invalid_request", f"status 는 {sorted(STATUSES)} 중 하나")
    row = ledger.row_to_dict(conn.execute("SELECT * FROM customization WHERE id = ?", (cid,)).fetchone())
    if row is None:
        raise ToolError("not_found", f"커스텀 없음: {cid}")
    history = row["history"] + [{"status": status, "note": note, "at": _now(), "by": actor}]
    with ledger.tx(conn):
        conn.execute("UPDATE customization SET status = ?, history = ? WHERE id = ?", (status, ledger.dumps(history), cid))
    return {"customization_id": cid, "status": status}


# ---------------------------------------------------------------- 버전 비교

def _route_key(f: dict) -> tuple[str, str]:
    path = re.sub(r"\{[^}]*\}|:\w+|<[^>]*>|\[[^\]]*\]", "{}", f["full_path"] or f["name"].strip("\"'"))
    return (f["method"] or "*", path.rstrip("/") or "/")


def _sym_table(conn, proj: dict) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in conn.execute("SELECT * FROM symbol ORDER BY path, start_line").fetchall():
        d = dict(r)
        d["local"] = local_name(proj["id"], d["qualified_name"])
        d.update(fingerprint(proj, d))
        out[(d["local"], d["label"])].append(d)
    return out


def compare_versions(settings: Settings, base_id: str, head_id: str, limit: int = 300) -> dict:
    """base(예: 우리가 커스텀한 버전) → head(업스트림 새 버전). 둘 다 인벤토리가 있어야 한다."""
    bconn, base = project_mod.load(settings, base_id)
    hconn, head = project_mod.load(settings, head_id)
    for p in (base, head):
        if not p["inventory_at"]:
            raise ToolError("invalid_request", f"{p['id']} 인벤토리 없음", recovery=f"oss-analyzer inventory {p['id']}")

    bfiles = {r["path"]: r["sha"] for r in bconn.execute("SELECT path, sha FROM file").fetchall()}
    hfiles = {r["path"]: r["sha"] for r in hconn.execute("SELECT path, sha FROM file").fetchall()}
    files = {"added": sorted(set(hfiles) - set(bfiles)), "removed": sorted(set(bfiles) - set(hfiles)),
             "modified": sorted(p for p in set(bfiles) & set(hfiles) if bfiles[p] != hfiles[p])}

    bsyms, hsyms = _sym_table(bconn, base), _sym_table(hconn, head)
    added, removed, sig_changed, body_changed = [], [], [], []
    pairs: dict[str, dict] = {}  # base 심볼 id → head 심볼
    for key in set(bsyms) | set(hsyms):
        bl, hl = bsyms.get(key, []), hsyms.get(key, [])
        for b, h in zip(bl, hl):  # 오버로드는 순서대로 짝짓기
            pairs[b["id"]] = h
            if b["signature"] != h["signature"]:
                sig_changed.append({"name": key[0], "label": key[1], "base": b["signature"], "head": h["signature"],
                                    "head_at": f"{h['path']}:{h['start_line']}"})
            elif b["body"] != h["body"]:
                body_changed.append({"name": key[0], "label": key[1], "head_at": f"{h['path']}:{h['start_line']}"})
        removed += bl[len(hl):]
        added += hl[len(bl):]
    # 이름 변경: 이름을 지운 본문이 같은 (제거, 추가) 짝
    renamed = []
    by_anon = defaultdict(list)
    for h in added:
        by_anon[(h["body_anon"], h["label"])].append(h)
    for b in list(removed):
        cands = by_anon.get((b["body_anon"], b["label"])) or []
        if len(cands) == 1 and b["body_anon"]:
            h = cands.pop()
            removed.remove(b)
            added.remove(h)
            pairs[b["id"]] = h
            renamed.append({"base": b["local"], "head": h["local"], "label": b["label"], "head_at": f"{h['path']}:{h['start_line']}"})

    # 심볼 밖(모듈 최상위 상수·설정·import) 변경 — 심볼 비교로는 안 보이는 부분
    module_level = []
    bcat = {r["path"]: r["category"] for r in bconn.execute("SELECT path, category FROM file").fetchall()}
    for path in files["modified"]:
        if bcat.get(path) not in ("code", "test"):
            continue
        sides = []
        for conn_, proj_ in ((bconn, base), (hconn, head)):
            spans = [(r["start_line"], r["end_line"]) for r in conn_.execute(
                "SELECT start_line, end_line FROM symbol WHERE path = ?", (path,)).fetchall()]
            try:
                lines = evidence.read_lines(proj_, path)
            except (OSError, ToolError):
                lines = []
            # 심볼 바로 위 데코레이터·어노테이션(@…)은 심볼 쪽으로 본다
            sides.append([re.sub(r"\s+", " ", ln).strip() for i, ln in enumerate(lines, start=1)
                          if ln.strip() and not any(a <= i <= b or (a - 3 <= i < a and ln.lstrip().startswith("@"))
                                                    for a, b in spans)])
        before, after = sides
        if before != after:
            module_level.append({"path": path, "removed": [x for x in before if x not in after][:10],
                                 "added": [x for x in after if x not in before][:10]})

    # 라우트
    def routes(conn) -> dict:
        return {_route_key(f): f for f in ledger.rows_to_dicts(conn.execute(
            "SELECT * FROM feature WHERE kind = 'http' AND in_test = 0").fetchall())}
    br, hr = routes(bconn), routes(hconn)
    route_diff = {"added": [f"{k[0]} {k[1]}" for k in sorted(set(hr) - set(br))],
                  "removed": [f"{k[0]} {k[1]}" for k in sorted(set(br) - set(hr))]}

    # 확장 포인트
    def xps(conn) -> set:
        return {(r["kind"], r["name"]) for r in conn.execute("SELECT kind, name FROM extension_point").fetchall()}
    bx, hx = xps(bconn), xps(hconn)

    # 커스텀 충돌: 앵커 기준
    changed_by_base: dict[str, str] = {}
    sig_names = {(c["name"], c["label"]) for c in sig_changed}
    body_names = {(c["name"], c["label"]) for c in body_changed}
    renamed_map = {r["base"]: r for r in renamed}
    for key, bl in bsyms.items():
        for b in bl:
            if b["id"] not in pairs:
                changed_by_base[b["id"]] = "removed"
            elif b["local"] in renamed_map:
                changed_by_base[b["id"]] = "renamed"
            elif key in sig_names:
                changed_by_base[b["id"]] = "signature-changed"
            elif key in body_names:
                changed_by_base[b["id"]] = "body-changed"
    conflicts = []
    for c in ledger.rows_to_dicts(bconn.execute("SELECT * FROM customization WHERE status != 'dropped'").fetchall()):
        for a in c["anchors"]:
            state = changed_by_base.get(a["symbol_id"])
            if state is None:
                continue
            h = pairs.get(a["symbol_id"])
            conflicts.append({"customization_id": c["id"], "description": c["description"], "kind": c["kind"],
                              "anchor": a["name"], "state": state,
                              "head": ({"name": h["local"], "at": f"{h['path']}:{h['start_line']}", "signature": h["signature"]}
                                       if h else None),
                              "severity": "high" if state in ("removed", "signature-changed", "renamed") else "medium"})

    # 결론 재검증: base 근거 위치가 head 에서 같은 내용인지 (스니펫 해시)
    stale_claims = []
    changed_paths = set(files["modified"]) | set(files["removed"])
    for cl in ledger.rows_to_dicts(bconn.execute("SELECT id, text, evidence_ids, symbol_ids FROM claim "
                                                 "WHERE review_status != 'rejected'").fetchall()):
        reasons = [f"심볼 {changed_by_base[s]}" for s in cl["symbol_ids"] if s in changed_by_base]
        for ev_id in cl["evidence_ids"]:
            try:
                ev = evidence.get(bconn, ev_id)
            except ToolError:
                continue
            for loc in ev["locations"]:
                if loc["path"] not in changed_paths:
                    continue
                try:
                    evidence.validate_location(hconn, head, loc)
                except ToolError:
                    reasons.append(f"근거 {loc['path']}:{loc['start_line']}-{loc['end_line']} 내용 바뀜")
        if reasons:
            stale_claims.append({"claim_id": cl["id"], "text": cl["text"][:200], "reasons": sorted(set(reasons))})

    # 바뀐 공개 계약(시그니처·제거·이름 변경)의 base 호출자 → head 에서 손봐야 할 후보
    callers = defaultdict(set)
    for e in bconn.execute("SELECT caller, callee FROM call_edge").fetchall():
        callers[local_name(base["id"], e["callee"])].add(local_name(base["id"], e["caller"]))
    contract = [c["name"] for c in sig_changed] + [b["local"] for b in removed] + [r["base"] for r in renamed]
    caller_review = {n: sorted(callers[n]) for n in contract if callers.get(n)}

    # 재사용 가능한 단위: 구성 파일이 전부 그대로
    unchanged = set(bfiles) - changed_paths
    reusable = [u["title"] for u in ledger.rows_to_dicts(bconn.execute(
        "SELECT title, members, status FROM unit WHERE status = 'completed'").fetchall())
        if all(m["path"] in unchanged for m in u["members"])]

    def cap(items: list) -> list:
        return items[:limit]
    return {
        "base": {"id": base["id"], "commit": base["commit_sha"]}, "head": {"id": head["id"], "commit": head["commit_sha"]},
        "summary": {"files_added": len(files["added"]), "files_removed": len(files["removed"]),
                    "files_modified": len(files["modified"]), "symbols_added": len(added), "symbols_removed": len(removed),
                    "renamed": len(renamed), "signature_changed": len(sig_changed), "body_changed": len(body_changed),
                    "module_level_changed": len(module_level),
                    "routes_added": len(route_diff["added"]), "routes_removed": len(route_diff["removed"]),
                    "customization_conflicts": len(conflicts), "stale_claims": len(stale_claims),
                    "reusable_completed_units": len(reusable)},
        "files": {k: cap(v) for k, v in files.items()},
        "symbols": {"added": cap([{"name": h["local"], "label": h["label"], "at": f"{h['path']}:{h['start_line']}"} for h in added]),
                    "removed": cap([{"name": b["local"], "label": b["label"], "at": f"{b['path']}:{b['start_line']}"} for b in removed]),
                    "renamed": cap(renamed), "signature_changed": cap(sig_changed), "body_changed": cap(body_changed),
                    "module_level_changed": cap(module_level)},
        "routes": route_diff,
        "extension_points": {"added": sorted(f"{k}:{n}" for k, n in hx - bx), "removed": sorted(f"{k}:{n}" for k, n in bx - hx)},
        "customization_conflicts": conflicts, "stale_claims": cap(stale_claims),
        "callers_to_review": caller_review, "reusable_completed_units": cap(reusable),
        "limitations": ["심볼 짝짓기는 (모듈 경로 포함 이름, 종류) 기준 — 파일 이동은 이름 변경 탐지(본문 동일)로만 잡힘",
                        "본문을 고치면서 이름도 바꾼 경우는 제거+추가로 보임",
                        "호출자 목록은 base 구조 지도(L0) 기준"],
    }


def workspace_report(settings: Settings, head_id: str, base_id: str, data: dict) -> Path:
    out = project_mod.workspace(settings, head_id) / "reports" / f"compare-{base_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ledger.dumps(data), encoding="utf-8")
    return out
