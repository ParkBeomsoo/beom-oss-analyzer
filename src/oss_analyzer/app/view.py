"""앱 화면용 데이터 — 원장·실행 기록을 사람이 보는 모양으로 정리한다 (화면 기술과 무관, 테스트 가능)."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import inbox, port, project, runner
from oss_analyzer.core.checkpoints import Checkpoints

STAGES = {
    "request": ["요구사항", "합격 기준", "계획", "구현", "검증", "인수인계"],
    "oss": ["분석", "범위", "원본 기록", "계획", "구현", "검증", "인수인계"],
    "plan": ["질문", "기획서", "다듬기"],
    "free": ["대화"],
}
MODE_TEXT = {"request": "요청사항", "oss": "오픈소스", "plan": "기획", "free": "자유 대화"}
TOOL_TEXT = {
    "oss_make_requirements": "요구사항 정리", "oss_port_capture": "합격 기준 저장", "oss_port_verify": "검증 실행",
    "oss_port_map": "구현 위치 기록", "oss_port_status": "진행 상태 확인", "oss_plan_set": "계획 작성",
    "oss_plan_progress": "계획 진행 표시", "oss_handoff": "인수인계 작성", "oss_port_slice": "옮길 범위 계산",
    "oss_port_create": "원장 만들기", "oss_features": "기능 목록 확인", "oss_person_ask": "질문",
    "oss_port_accept": "차이 허용 제안", "oss_port_finish": "완료 판정", "oss_inventory_build": "저장소 분석",
}


GATE_LABEL = {
    port.GATE_MAPPED: ("모든 요구사항 구현", "옮길 항목 모두 처리"),
    port.GATE_TARGETS: ("구현 위치가 그대로 있음", "옮긴 위치가 그대로 있음"),
    port.GATE_DIFF: ("검증 통과 (지금 코드 기준)", "원본과 같음 (지금 코드 기준)"),
    port.GATE_ADEQUACY: ("케이스가 구현 코드를 모두 실행", "케이스가 옮길 코드를 모두 실행"),
    port.GATE_HUMAN: ("사람 확인 모두 승인", "사람 확인 모두 승인"),
    port.GATE_INTEGRITY: ("기록 무결", "기록 무결"),
    port.GATE_LICENSE: ("라이선스 문제 없음", "라이선스 문제 없음"),
    port.GATE_REQ_CASES: ("요구사항마다 합격 기준", "요구사항마다 합격 기준"),
}


def sig(value) -> str:
    """화면에 보여 준 내용의 지문 — 승인은 이 지문이 지금 내용과 같을 때만 기록한다."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def plan_core(plan: dict) -> dict:
    return {"steps": [{k: s.get(k) for k in ("title", "files", "commands", "requirements")} for s in plan.get("steps") or []],
            "summary": plan.get("summary"), "design": plan.get("design"), "at": plan.get("at")}


def _status(run: dict, asks: int, reviews: int, alive: bool = True) -> tuple[str, str]:
    s = run.get("status")
    if s in ("running", "waiting_answer") and not alive and not (asks or reviews):
        return "중단됨", "idle"  # 진행하던 실행기가 사라짐 (앱을 닫았거나 비정상 종료) — 이어서 진행으로 재개
    if s not in ("done", "error") and (asks or reviews or s in ("waiting_approval", "waiting_plan")):
        return "내 차례", "turn"
    if s == "running":
        return "실행 중", "run"
    if s == "done":
        return ("완료", "ok") if run.get("accepted") else ("내 차례", "turn")
    if s == "stopped_max_rounds":
        return "확인 필요", "check"
    if s == "error":
        return "실패", "fail"
    if s == "waiting_input":
        return ("내 차례", "turn") if alive else ("중단됨", "idle")
    if s == "created":
        return "준비됨", "idle"
    if s == "preparing":
        return "분석 중", "run"
    return "중단됨", "idle"


def _stage(run: dict, st: dict | None) -> int:
    mode = run["mode"]
    last = len(STAGES[mode]) - 1
    if mode == "free":
        return 0
    if mode == "plan":
        return 2 if run.get("handoff") and run.get("round", 0) > 1 else 1 if run.get("round") else 0
    if run.get("status") == "done" or run.get("handoff"):
        return last
    if st is None:
        return 0
    g, plan = st["gates"], run.get("plan") or {}
    if mode == "request":
        req = st.get("requirements") or {}
        if not req.get("total"):
            return 0
        if not g.get(port.GATE_REQ_CASES) or (st["approvals"]["pending"] and not req.get("implemented")):
            return 1
        if not plan.get("approved"):
            return 2
        if not g[port.GATE_MAPPED]:
            return 3
        return 4
    if st["counts"].get("pending"):
        return 1
    if st.get("last_check") is None and "capture" in st["next_step"]:
        return 2
    if not plan.get("approved"):
        return 3
    if not g[port.GATE_MAPPED] or st.get("last_check") is None:
        return 4
    return 5


_SHELL_WRAP = re.compile(r"""^\s*"?[^"]*?(?:powershell|pwsh|cmd|bash|sh)(?:\.exe)?"?\s+(?:-NoProfile\s+)?(?:-Command|-c|/c)\s+(.*)$""", re.I | re.S)


def _inner_command(cmd: str) -> str:
    """에이전트가 셸로 감싼 명령에서 실제 명령만 — '"...powershell.exe" -Command 'npm test'' → 'npm test'."""
    m = _SHELL_WRAP.match(cmd)
    if not m:
        return cmd
    inner = m.group(1).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "'\"":
        inner = inner[1:-1]
    return inner


def _tool_label(name: str | None, raw: str | None) -> str:
    name = name or ""
    short = name.split("__")[-1]
    if short in TOOL_TEXT:
        return TOOL_TEXT[short]
    try:
        inp = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        inp = {}
    path = (inp or {}).get("file_path") or (inp or {}).get("path") or ""
    tail = path.replace("\\", "/").split("/")[-1] if path else ""
    if short in ("Write", "Edit", "MultiEdit"):
        return f"{tail} 수정" if tail else "파일 수정"
    if short in ("Read",):
        return f"{tail} 읽기" if tail else "파일 읽기"
    if short in ("Glob", "Grep", "LS"):
        return "코드 찾아보기"
    if short in ("Bash", "PowerShell"):
        cmd = _inner_command(str((inp or {}).get("command", "")))[:80]
        return f"명령 실행 · {cmd}" if cmd else "명령 실행"
    if short in ("WebFetch", "WebSearch"):
        return "웹 확인"
    if short in ("TodoWrite",):
        return "할 일 정리"
    return short or "도구"


def feed(settings, run: dict) -> list[dict]:
    evs, _ = inbox.events(settings, run["id"])
    out: list[dict] = []
    if run.get("request"):
        out.append({"type": "request", "text": run["request"], "feature": run.get("feature")})
    elif run.get("feature"):
        out.append({"type": "request", "text": run["feature"]})
    for e in evs:
        k = e.get("kind")
        before = len(out)
        if k == "round_start":
            out.append({"type": "divider", "text": f"라운드 {e.get('round')}"})
        elif k == "agent":
            for it in e.get("items") or []:
                if it.get("text"):
                    out.append({"type": "message", "text": it["text"]})
                elif it.get("tool") and not str(it["tool"]).endswith(("oss_activity", "oss_port_status")):
                    label = _tool_label(it["tool"], it.get("input"))
                    if out and out[-1]["type"] == "tools":
                        if label not in out[-1]["items"]:
                            out[-1]["items"].append(label)
                    else:
                        out.append({"type": "tools", "items": [label]})
        elif k == "user_message":
            out.append({"type": "user", "text": e.get("text", "")})
        elif k == "checkpoint":
            out.append({"type": "checkpoint", "id": e.get("id"), "label": e.get("label")})
        elif k == "approved":
            out.append({"type": "note", "text": f"승인함 · {len(e.get('items') or [])}건"})
        elif k == "plan_approved":
            out.append({"type": "note", "text": "계획 승인함"})
        elif k == "restored":
            out.append({"type": "note", "text": f"체크포인트로 되돌림 · {e.get('label', '')}"})
        elif k == "permission" and e.get("decision") == "refuse":
            out.append({"type": "note", "tone": "warn", "text": f"규칙상 거절된 작업 · {e.get('tool')}"})
        elif k == "agent_result" and e.get("is_error"):
            out.append({"type": "note", "tone": "fail", "text": f"라운드가 오류로 끝남 · {e.get('subtype') or e.get('result') or ''}"[:300]})
        elif k in ("agent_error", "error"):
            out.append({"type": "note", "tone": "fail", "text": (e.get("message") or e.get("stderr") or "오류")[-300:]})
        elif k == "finished":
            out.append({"type": "note", "tone": "ok", "text": "완료 조건을 모두 채웠습니다 — 완료 판정 기록"})
        elif k == "max_rounds":
            out.append({"type": "note", "tone": "warn", "text": "정한 반복 횟수를 모두 썼습니다 — 메시지를 보내면 이어서 진행합니다"})
        elif k == "note" and e.get("text"):
            out.append({"type": "note", "tone": e.get("tone") or "", "text": str(e["text"])[:1000], "at": e.get("at")})
        elif k == "stalled":
            out.append({"type": "note", "tone": "warn", "text": "두 라운드 연속 진행이 없어 멈췄습니다 — 에이전트 응답을 확인하고 지시를 보내 주세요"})
        for item in out[before:]:
            item.setdefault("at", e.get("at"))
    return out[-400:]


def _reviews(reviews: list[dict], conn, port_id: str) -> dict:
    cap = port._last_check(conn, port_id, "capture")
    exps = (cap or {}).get("detail", {}).get("expectations") or {}
    req_of: dict[str, list[str]] = {}
    for r, cases in ((cap or {}).get("detail", {}).get("req_cover") or {}).items():
        for c in cases:
            req_of.setdefault(c, []).append(r)
    out = {"requirements": [], "expectations": [], "divergences": [], "omitted": [], "case_changes": [], "count": len(reviews)}
    for h in reviews:
        if h["kind"] == "requirements":
            out["requirements"] = [{"id": d["case"], "text": d["diff"]} for d in h["detail"]]
        elif h["kind"] == "expectation":
            name = h["item_id"].split(":", 1)[1]
            ex = exps.get(name) or {}
            out["expectations"].append({"case": name, "description": ex.get("description"), "expect": ex.get("expect"),
                                        "new": ex.get("new"), "input": ex.get("input"),
                                        "order": ex.get("order", 0), "source": ex.get("source"), "reqs": req_of.get(name, [])})
        elif h["kind"] == "divergence":
            out["divergences"].append({"summary": h["summary"], "detail": h.get("detail")})
        elif h["kind"] == "omitted":
            out["omitted"].append({"summary": h["summary"].removeprefix("생략: ")})
        else:
            out["case_changes"].append(h.get("detail"))
    # 케이스 파일 순서대로, 순서 기록이 없으면 숫자를 숫자로 비교 (#10 이 #2 앞에 오지 않게)
    out["expectations"].sort(key=lambda e: (e["order"], [(0, int(t), "") if t.isdigit() else (1, 0, t)
                                                          for t in re.split(r"(\d+)", e["case"]) if t]))
    return out


GATE_HELP = {
    port.GATE_MAPPED: "요구사항(또는 옮길 항목)마다 구현했는지·뺐는지 정해졌다",
    port.GATE_TARGETS: "구현했다고 적은 코드 위치(파일:줄)가 지금도 그대로 있다",
    port.GATE_DIFF: "승인된 합격 케이스를 지금 코드로 실제 실행해 기대 결과와 모두 같았다",
    port.GATE_ADEQUACY: "케이스를 돌리는 동안 구현한 코드가 실제로 실행됐다 (커버리지) — 케이스가 헛돌지 않았다",
    port.GATE_HUMAN: "사람이 확인해야 할 항목(요구사항·합격 기준·차이 허용)이 모두 승인됐다",
    port.GATE_INTEGRITY: "원장·골든 기록이 툴킷 밖에서 고쳐지지 않았다 (해시 사슬)",
    port.GATE_LICENSE: "가져온 코드의 라이선스 문제가 없다",
    port.GATE_REQ_CASES: "요구사항마다 그것을 확인하는 합격 케이스가 하나 이상 있다",
}


def _spec_view(conn, port_id: str, rows: list[dict], last: dict | None) -> dict:
    """합의한 것(요구사항·합격 기준·승인)과 검증한 것(케이스별 입력 → 기대 → 실제)을 한 화면에."""
    cap = port._last_check(conn, port_id, "capture") or {}
    exps = (cap.get("detail") or {}).get("expectations") or {}
    cover = (cap.get("detail") or {}).get("req_cover") or {}
    result = {r["name"]: r for r in rows}
    cases = []
    for name, e in sorted(exps.items(), key=lambda kv: kv[1].get("order", 0)):
        r = result.get(name)
        cases.append({"name": name, "description": e.get("description"), "input": e.get("input"), "expect": e.get("expect"),
                      "reqs": [k for k, v in cover.items() if name in v],
                      "ok": r["ok"] if r else None, "actual": (r or {}).get("actual"), "diffs": (r or {}).get("diffs") or [],
                      "screenshot": (r or {}).get("screenshot")})
    appr = conn.execute("SELECT at, payload FROM port_log WHERE port_id = ? AND action = 'approve' ORDER BY rowid DESC LIMIT 1",
                        (port_id,)).fetchone()
    who = None
    if appr:
        try:
            p = json.loads(appr["payload"] or "{}")
            who = {"at": appr["at"], "by": p.get("approved_by"), "channel": p.get("channel")}
        except ValueError:
            who = {"at": appr["at"]}
    return {"cases": cases, "approved": who, "verified_at": (last or {}).get("created_at"), "gates_help": GATE_HELP}


# ---------------------------------------------------------------- 다시 계산 줄이기
# 앱은 1.5초마다 목록·상세를 묻는다. 실행 파일·이벤트·질문함·원장·잠금이 그대로면 지난 결과를 그대로 쓴다.
_MEMO: dict = {}


def _stat(p: Path) -> tuple | None:
    try:
        st = p.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _run_stamp(settings, run: dict) -> tuple:
    d = inbox.run_dir(settings, run["id"])
    out = [_stat(d / "run.json"), _stat(d / "events.jsonl"), _stat(d / "inbox"), _stat(d / runner.LOCK_FILE)]
    if run.get("project_id"):
        ws = project.workspace(settings, run["project_id"])
        out += [_stat(ws / "ledger.db"), _stat(ws / "ledger.db-wal")]
    return tuple(out)


def _memo(key, stamp, fn, max_age: float | None = None):
    hit = _MEMO.get(key)
    now = time.monotonic()
    if hit and hit[0] == stamp and (max_age is None or now - hit[2] < max_age):
        return hit[1]
    value = fn()
    _MEMO[key] = (stamp, value, now)
    return value


def summary(settings, run: dict) -> dict:
    return dict(_memo(("summary", run["id"]), _run_stamp(settings, run), lambda: _summary(settings, run)))


def _summary(settings, run: dict) -> dict:
    asks = len(inbox.pending(settings, run["id"]))
    reviews = 0
    if run.get("port_id") and run.get("status") not in ("done",):
        try:
            reviews = len(port.pending_reviews(project.load(settings, run["project_id"])[0], run["port_id"]))
        except ToolError:
            reviews = 0
    label, tone = _status(run, asks, reviews, runner.lock_owner(settings, run["id"]) is not None)
    return {"id": run["id"], "name": run.get("name"), "mode": run["mode"], "mode_text": MODE_TEXT[run["mode"]], "archived": bool(run.get("archived")),
            "status": label, "tone": tone, "created_at": run.get("created_at"), "agent": run.get("agent", "claude")}


def runs(settings) -> list[dict]:
    return [summary(settings, r) for r in inbox.list_runs(settings)]


def detail(settings, run_id: str) -> dict:
    run = inbox.load_run(settings, run_id)
    # 대상 폴더를 사람이 직접 고친 것(지문)은 기록 파일에 안 남으니 10초마다는 새로 본다
    return dict(_memo(("detail", run_id), _run_stamp(settings, run), lambda: _detail(settings, run_id), max_age=10))


def _detail(settings, run_id: str) -> dict:
    run = inbox.load_run(settings, run_id)
    try:
        st = runner.status(settings, run)
    except ToolError:
        st = None
    conn = project.load(settings, run["project_id"])[0] if st else None
    reviews = port.pending_reviews(conn, run["port_id"]) if conn is not None else []
    asks = inbox.pending(settings, run_id)
    label, tone = _status(run, len(asks), len(reviews), runner.lock_owner(settings, run_id) is not None)
    mode = run["mode"]
    out = {"run": {k: run.get(k) for k in ("id", "name", "mode", "status", "round", "max_rounds", "target_root", "request",
                                           "feature", "agent", "model", "effort", "accepted", "cost_usd", "created_at")},
           "mode_text": MODE_TEXT[mode], "status": label, "tone": tone, "stages": STAGES[mode], "stage": _stage(run, st),
           "activity": run.get("activity"), "plan": run.get("plan"), "handoff": run.get("handoff"), "asks": asks,
           "feed": feed(settings, run), "messages": run.get("messages") or [],
           "plan_sig": sig(plan_core(run["plan"])) if (run.get("plan") or {}).get("steps") else None,
           "policy": run.get("policy") or {}, "perm_mode": inbox.mode_of(run.get("policy") or {}),
           "plan_approval": run.get("plan_approval", True), "confirm_at": run.get("confirm_at", "start")}
    if st:
        last = st.get("last_check")
        rows = (port._last_check(conn, run["port_id"], "differential:%") or {}).get("detail", {}).get("rows", []) if last else []
        cap = (port._last_check(conn, run["port_id"], "capture") or {}).get("detail", {}).get("expectations") or {}
        out.update(gates=[{"name": k, "label": GATE_LABEL.get(k, (k, k))[0 if mode == "request" else 1], "ok": v}
                          for k, v in st["gates"].items()], next_step=st["next_step"],
                   reviews=_reviews(reviews, conn, run["port_id"]) if reviews else None, review_sig=sig(reviews),
                   requirements=[{"id": r["item_id"], "text": r["label"], "status": r["status"], "target": r["target_ref"]}
                                 for r in conn.execute("SELECT item_id, label, status, target_ref FROM port_map WHERE port_id = ? "
                                                       "AND kind = 'requirement'", (run["port_id"],)).fetchall()],
                   checks={"passed": last["passed"] if last else None, "cases": last["cases"] if last else None,
                           "rows": [{"name": r["name"], "ok": r["ok"], "diffs": (r.get("diffs") or [])[:2],
                                     "generated": bool(r.get("generated")), "expected": r["expected"] if isinstance(r.get("expected"), dict) else (cap.get(r["name"]) or {}).get("expect"),
                                     "input": (cap.get(r["name"]) or {}).get("input")} for r in rows]})
        out["requirements"].sort(key=lambda r: int(r["id"][1:]) if r["id"][1:].isdigit() else 0)
        out["spec"] = _spec_view(conn, run["port_id"], rows, last)
        if mode == "oss":
            c = st["counts"]
            out["items"] = {"total": sum(c.values()), "done": sum(c.values()) - c.get("pending", 0)}
    cps = Checkpoints(inbox.run_dir(settings, run_id), run["target_root"])
    out["checkpoints"] = cps.list()[:30]
    return out


def changes(settings, run_id: str) -> dict:
    run = inbox.load_run(settings, run_id)
    return Checkpoints(inbox.run_dir(settings, run_id), run["target_root"]).changes()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
