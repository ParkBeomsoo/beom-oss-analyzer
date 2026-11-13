"""자동 실행용 도구 — 실행기가 띄운 Claude Code 에서만 쓰인다(OSS_ANALYZER_RUN). 권한 판단과 사람에게 묻기."""
from __future__ import annotations

import json
import os
from pathlib import Path

from oss_analyzer.core import inbox
from oss_analyzer.server import mcp, settings


def _run() -> dict | None:
    rid = os.environ.get("OSS_ANALYZER_RUN")
    return inbox.load_run(settings, rid) if rid else None


@mcp.tool()
def oss_action_approve(tool_name: str, input: dict, tool_use_id: str | None = None) -> str:  # noqa: A002 — Claude Code 가 정한 이름
    """Claude Code 의 권한 요청 처리기(--permission-prompt-tool). 종류별 규칙으로 허용·거절하고, 물어봐야 하면 앱의 승인 카드로 묻는다."""
    run = _run()
    if run is None:
        return json.dumps({"behavior": "deny", "message": "자동 실행 밖에서는 쓸 수 없음"}, ensure_ascii=False)
    root = Path(run["target_root"])
    kind = inbox.classify(tool_name, input or {}, root, [settings.home])
    decision = run["policy"].get(kind, "ask")
    summary = f"{inbox.KINDS.get(kind, kind)}: {tool_name} {json.dumps(input, ensure_ascii=False)[:300]}"
    inbox.log(settings, run["id"], "permission", tool=tool_name, permission_kind=kind, decision=decision)
    if decision == "ask":
        ans = inbox.ask(settings, run["id"], "permission", {"summary": summary, "tool": tool_name, "input": input,
                                                            "permission_kind": kind})
        if ans.get("always") and ans.get("allow"):
            inbox.update_run(settings, run["id"], lambda r: r["policy"].__setitem__(kind, "allow"))
        decision = "allow" if ans.get("allow") else "refuse"
    if decision == "allow":
        return json.dumps({"behavior": "allow", "updatedInput": input}, ensure_ascii=False)
    return json.dumps({"behavior": "deny", "message": f"규칙상 허용되지 않음 ({inbox.KINDS.get(kind, kind)}). 다른 방법을 찾거나 "
                                                      "oss_person_ask 로 사용자에게 이유와 함께 물어본다."}, ensure_ascii=False)


@mcp.tool()
def oss_person_ask(question: str, choices: list[str] | None = None) -> dict:
    """사용자에게 묻고 답을 기다린다(앱의 질문 카드). 요구사항이 애매하거나 결정이 필요할 때. 답이 없으면 timeout."""
    run = _run()
    if run is None:
        return {"ok": False, "error": "자동 실행 밖에서는 사용자에게 직접 묻는다"}
    ans = inbox.ask(settings, run["id"], "question", {"question": question, "choices": choices or []})
    return {"ok": not ans.get("timeout"), "answer": ans.get("text") or ans.get("choice"), "timeout": bool(ans.get("timeout"))}


def _update(fn) -> dict:
    run = _run()
    if run is None:
        return {"ok": False, "error": "자동 실행 밖"}
    inbox.update_run(settings, run["id"], fn)
    return {"ok": True}


@mcp.tool()
def oss_activity(text: str) -> dict:
    """지금 하는 일을 한 줄로 알린다 (앱 상단의 활동 표시). 예: "order.rs 에 할인 계산 추가 중"."""
    out = _update(lambda r: r.update(activity={"text": text.strip()[:200], "at": inbox._now()}))
    if out["ok"]:
        inbox.log(settings, os.environ["OSS_ANALYZER_RUN"], "activity", text=text.strip()[:200])
    return out


@mcp.tool()
def oss_plan_set(steps: list[dict], summary: str = "") -> dict:
    """구현 계획을 낸다 — 사용자가 앱에서 승인한 뒤 구현한다. steps: [{title, files?: [...], commands?: [...], requirements?: ["R1"]}].
    계획을 다시 내면 이전 승인은 풀린다."""
    clean = [{"title": str(s.get("title", "")).strip()[:200], "files": [str(f) for f in s.get("files") or []][:20],
              "commands": [str(c) for c in s.get("commands") or []][:10], "requirements": s.get("requirements") or [],
              "status": "todo"} for s in steps if str(s.get("title", "")).strip()]
    if not clean:
        return {"ok": False, "error": "단계가 비어 있음"}

    def put(r):
        old = r.get("plan") or {}
        history = (old.get("history") or []) + ([{"steps": old["steps"], "summary": old.get("summary")}] if old.get("steps") else [])
        r["plan"] = {"steps": clean, "summary": summary.strip()[:1000], "approved": False, "history": history[-5:],
                     "at": inbox._now()}
    out = _update(put)
    if out["ok"]:
        inbox.log(settings, os.environ["OSS_ANALYZER_RUN"], "plan", steps=len(clean))
    return {**out, "note": "사용자 승인 전에는 코드를 바꾸지 않는다"}


@mcp.tool()
def oss_plan_progress(index: int, status: str, note: str = "") -> dict:
    """계획 단계 진행 표시. index 는 0부터. status: todo | doing | done | skipped."""
    if status not in ("todo", "doing", "done", "skipped"):
        return {"ok": False, "error": "status 는 todo|doing|done|skipped"}

    def put(r):
        steps = (r.get("plan") or {}).get("steps") or []
        if 0 <= index < len(steps):
            steps[index]["status"] = status
            if note:
                steps[index]["note"] = note.strip()[:300]
    return _update(put)


@mcp.tool()
def oss_handoff(summary: str, how_to_run: str, risks: str = "", follow_ups: str = "") -> dict:
    """인수인계 — 완료 조건을 채운 뒤 남긴다. 바뀐 것 요약, 실행 방법(명령·URL), 남은 위험·미검증, 이어서 할 일."""
    out = _update(lambda r: r.update(handoff={"summary": summary.strip()[:4000], "how_to_run": how_to_run.strip()[:2000],
                                              "risks": risks.strip()[:2000], "follow_ups": follow_ups.strip()[:2000],
                                              "at": inbox._now()}))
    if out["ok"]:
        inbox.log(settings, os.environ["OSS_ANALYZER_RUN"], "handoff")
    return out


@mcp.tool()
def oss_run_policy(changes: dict | None = None) -> dict:
    """이 실행의 권한 규칙 보기, 또는 더 엄격하게만 바꾸기 ({종류: ask|refuse}). 느슨하게 하는 변경은 무시된다."""
    run = _run()
    if run is None:
        return {"ok": False, "error": "자동 실행 밖"}
    if changes:
        run = inbox.update_run(settings, run["id"], lambda r: r.update(policy=inbox.tighten(r["policy"], changes)))
    return {"ok": True, "policy": run["policy"], "kinds": inbox.KINDS}
