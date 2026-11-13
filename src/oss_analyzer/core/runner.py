"""자동 실행기 — 사용자 PC 의 Claude Code(`claude`)를 헤드리스로 돌려 단계를 끝까지 진행한다.

- 엔진은 사용자가 설치·로그인한 수정하지 않은 `claude` 바이너리. 계정 정보는 읽지 않는다.
  (API 키 모드를 고르면 ANTHROPIC_API_KEY 를 쓰고, 아니면 환경에서 그 키를 빼서 사용자 로그인으로 돈다)
- 도구는 이 툴킷 MCP 서버만 붙인다(--strict-mcp-config). 권한 요청은 oss_action_approve 로 와서 종류별 규칙
  (inbox.py)으로 판단하고, ask 만 앱의 승인 카드로 간다. 질문은 oss_person_ask → 질문 카드.
- 완료 판단은 에이전트가 아니라 툴킷: 라운드마다 원장 게이트를 읽어 다음 지시를 만들고, 사람 승인이 필요하면 멈춘다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings
from oss_analyzer.core import agents as agents_mod
from oss_analyzer.core import inbox, make, port, project
from oss_analyzer.core.checkpoints import Checkpoints

MCP_NAME = "oss-analyzer"
TOOL_PREFIX = f"mcp__{MCP_NAME}__"
APPROVE_TOOL = f"{TOOL_PREFIX}oss_action_approve"
READ_ONLY = ["Read", "Glob", "Grep", "LS", "TodoWrite"]
NEVER = ["AskUserQuestion", f"{TOOL_PREFIX}oss_port_approve", f"{TOOL_PREFIX}oss_run_answer"]


# ---------------------------------------------------------------- 에이전트 점검

def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for p in (Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude"),
              Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if p.is_file():
            return str(p)
    return _desktop_bundled()


def _desktop_bundled() -> str | None:
    """Claude 데스크톱 앱이 함께 설치한 Claude Code (수정되지 않은 공식 바이너리) — 가장 높은 버전."""
    bases = [Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code",
             Path.home() / "Library" / "Application Support" / "Claude" / "claude-code"]
    found = []
    for base in bases:
        if base.is_dir():
            found += [p for p in base.glob("*/*/" + ("claude.exe" if os.name == "nt" else "claude")) if p.is_file()]

    def ver(p: Path) -> tuple:
        try:
            return tuple(int(x) for x in p.parent.parent.name.split("."))
        except ValueError:
            return ()
    return str(max(found, key=lambda p: (ver(p), p.stat().st_mtime))) if found else None


def agent_status() -> dict:
    exe = find_claude()
    if not exe:
        return {"ready": False, "claude": None,
                "hint": "Claude Code CLI(claude)가 없음 — 공식 설치 안내대로 설치한 뒤 터미널에서 `claude auth login`"}
    try:
        proc = subprocess.run([exe, "auth", "status"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ready": False, "claude": exe, "hint": f"claude auth status 실패: {type(e).__name__}"}
    try:
        info = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        info = {}
    method = info.get("authMethod")
    out = {"ready": proc.returncode == 0 and method not in (None, "none"), "claude": exe, "auth_method": method}
    if not out["ready"]:
        out["hint"] = f'Claude Code 로그인 필요 — 터미널에서 "{exe}" auth login'
        out["login_command"] = [exe, "auth", "login"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        out["note"] = "ANTHROPIC_API_KEY 가 설정돼 있음 — 구독 로그인 모드에서는 실행할 때 이 키를 빼고 돈다"
    return out


# ---------------------------------------------------------------- 지시문

RULES = """너는 beom-oss-analyzer 툴킷의 자동 실행 단계에서 일하는 개발 에이전트다.
- 툴킷 도구(mcp__oss-analyzer__*)로 원장을 갱신하고, 각 응답의 next_step 을 따른다. 완료 판단은 툴킷이 한다.
- 사람 승인 항목(요구사항·기대값·차이 허용·생략)은 제안만 한다. 승인은 사람이 앱에서 한다 — 승인 도구를 쓰지 않는다.
- 사용자에게 물어볼 것은 mcp__oss-analyzer__oss_person_ask 로 묻는다. 추측으로 요구사항을 정하지 않는다.
  답을 받기 전에 라운드가 끝나면, 그 질문에 기대는 일은 하지 말고 끝낸다 — 답은 다음 라운드 지시로 온다. 같은 질문을 다시 하지 않는다.
- 기대값은 사용자 요청·원본에서 정하고, 구현 출력을 베껴 기대값으로 만들지 않는다.
- 합격 케이스 파일 형식: {"cases": [{"name": "이름", 입력, "req": "R1" 또는 ["R1", "R2"], "expect": {...}}]}
  입력 — CLI: "argv": ["3"] (+ "stdin"), HTTP: "method", "path", "json", 함수: "call" + "args". 
  expect — "status"(종료 코드 또는 HTTP 상태), "body"(출력 JSON 전체가 같음), "equals"({"a.b": 값} 일부 필드), "match"(정규식).
  정해진 키 밖의 키는 거절된다 (조용히 무시되면 다른 입력으로 검증되기 때문).
- 작업은 대상 폴더 안에서만 한다. 툴킷 원장·실행 폴더·설정을 직접 고치지 않는다.
- 지금 하는 일은 mcp__oss-analyzer__oss_activity 로 한 줄씩 알린다 (예: "order.rs 에 할인 계산 추가 중").
- 구현 전에는 계획(oss_plan_set: 단계마다 제목·바꿀 파일·실행할 명령)을 내고 승인을 기다린다. 구현 중에는 단계마다
  oss_plan_progress 로 진행을 표시한다.
- 완료 조건을 모두 채우면 oss_handoff 로 인수인계(바뀐 것, 실행 방법, 남은 위험)를 남긴다.
- 이번 라운드에서 할 수 있는 만큼 진행하고, 마지막에 한두 문장으로 한 일과 남은 일을 말한다."""


def _brief(run: dict, st: dict | None) -> str:
    lines = [f"[모드] {'요청사항 기반 만들기' if run['mode'] == 'request' else '오픈소스 기반 포팅'}",
             f"[대상 폴더] {run['target_root']}"]
    if run.get("request"):
        lines.append(f"[사용자 요청]\n{run['request']}")
    if run["mode"] == "oss":
        lines.append(f"[원본 프로젝트] {run.get('project_id')} (oss_* 도구의 project_id)")
        if run.get("feature"):
            lines.append(f"[옮길 기능] {run['feature']}")
        if run.get("source_run"):
            lines.append(f"[원본 실행 정보] {run['source_run']}")
    if run.get("context_project"):
        lines.append(f"[넣을 저장소 분석] project_id={run['context_project']} — 기존 구조·패턴을 먼저 보고 맞춘다")
    if st:
        lines.append(f"[원장] project_id={run['project_id']} port_id={run['port_id']}")
        lines.append(f"[다음 할 일] {st['next_step']}")
        unmet = [k for k, v in st["gates"].items() if not v]
        if unmet:
            lines.append("[미충족 조건] " + " / ".join(unmet))
        last = st.get("differential") or {}
        if last.get("why"):
            lines.append(f"[검증 상태] {last['why']}")
    elif run["mode"] == "oss":
        lines.append("[다음 할 일] oss_features·oss_port_slice 로 옮길 범위를 찾고 oss_port_create 로 원장을 만든다")
        if not run.get("feature") and not run.get("request"):
            lines.append("[옮길 기능 미정] 사용자가 기능을 정하지 않았다 — oss_features 로 기능 목록을 정리해 "
                         "oss_person_ask 의 choices 로 고르게 한 뒤 진행한다")
    plan = run.get("plan") or {}
    if plan.get("steps"):
        done = sum(1 for x in plan["steps"] if x.get("status") == "done")
        lines.append(f"[계획] {'승인됨' if plan.get('approved') else '승인 대기'} · {done}/{len(plan['steps'])} 단계 완료")
    msgs = [m for m in run.get("messages") or [] if not m.get("delivered")]
    if msgs:
        lines.append("[사용자 추가 요청 — 이번 라운드에 반영]\n" + "\n".join(f"- {m['text']}" for m in msgs))
    return "\n".join(lines)


# ---------------------------------------------------------------- 실행

def create(settings: Settings, *, mode: str, target_root: str, request: str = "", name: str | None = None,
           source: str | None = None, feature: str | None = None, source_run: str | None = None,
           context_project: str | None = None, max_rounds: int = 8, model: str | None = None,
           auth: str = "subscription", policy: dict | None = None, agent: str = "claude",
           plan_approval: bool = True, effort: str | None = None) -> dict:
    """실행 준비 — 요청사항 모드는 원장까지 만들고, 오픈소스 모드는 원본 저장소를 열어 둔다(인벤토리는 첫 단계에서)."""
    if mode not in ("request", "oss"):
        raise ToolError("invalid_request", "mode 는 request 또는 oss")
    Path(target_root).mkdir(parents=True, exist_ok=True)
    meta = {"mode": mode, "target_root": str(Path(target_root).resolve()), "request": request.strip(), "name": name or "project",
            "feature": feature, "source_run": source_run, "context_project": context_project, "max_rounds": max_rounds,
            "model": model, "auth": auth, "project_id": None, "port_id": None, "session_id": None, "policy": policy or {},
            "agent": agent, "plan_approval": plan_approval, "messages": [], "effort": effort or None}
    agents_mod.get(agent)  # 모르는 에이전트면 여기서 거절
    if mode == "request":
        if not request.strip():
            raise ToolError("invalid_request", "요청사항이 비어 있음")
        made = make.create(settings, name=meta["name"], request=request, target_root=target_root, context=context_project,
                           author="runner")
        meta.update(project_id=made["project_id"], port_id=made["port_id"])
    else:
        if not source:
            raise ToolError("invalid_request", "원본 저장소(source)가 필요함")
        opened = project.open_repo(settings, source, None, name)
        meta.update(project_id=opened["project"]["id"], source=source)
    return inbox.new_run(settings, meta)


def status(settings: Settings, run: dict) -> dict | None:
    if not run.get("port_id"):
        if run.get("project_id"):
            conn, _ = project.load(settings, run["project_id"])
            ports = port.list_(conn)["items"]
            if ports:
                run["port_id"] = ports[-1]["port_id"]
                inbox.update_run(settings, run["id"], lambda r: r.update(port_id=run["port_id"]))
        if not run.get("port_id"):
            return None
    conn, _ = project.load(settings, run["project_id"])
    return port.status_(conn, run["port_id"], brief=True)


LOCK_FILE = "runner.lock"
LOCK_BEAT_SEC = 10
LOCK_STALE_SEC = 45
STOP_FILE = "stop.request"


def request_stop(settings: Settings, run_id: str) -> None:
    """다른 프로세스에서 돌고 있는 실행기에 멈춤을 요청한다 (1초 안에 알아챈다)."""
    (inbox.run_dir(settings, run_id) / STOP_FILE).write_text(inbox._now(), encoding="utf-8")


def lock_owner(settings: Settings, run_id: str) -> dict | None:
    """이 실행을 지금 진행 중인 실행기 — 앱 스레드든 CLI 든 다른 앱 창이든. 심장 박동이 끊기면 없는 것으로 본다."""
    p = inbox.run_dir(settings, run_id) / LOCK_FILE
    try:
        if time.time() - p.stat().st_mtime > LOCK_STALE_SEC:
            return None
    except OSError:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}  # 막 만들어져 아직 비어 있는 잠금 — 주인이 있다


def kill_tree(proc: subprocess.Popen) -> None:
    """에이전트 프로세스와 그 자식까지 끝낸다 — Windows 의 .cmd 실행 파일은 terminate 로 껍데기만 죽고 node 가 남는다."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
    else:
        proc.terminate()


class Runner:
    """한 실행을 라운드 단위로 진행한다. 앱은 스레드로, CLI 는 그대로 돌린다."""

    def __init__(self, settings: Settings, run_id: str, exe=None):
        self.settings = settings
        self.run_id = run_id
        self.agent = agents_mod.get(inbox.load_run(settings, run_id).get("agent"), exe)
        self.exe = self.agent.exe
        self._stop = threading.Event()
        self.proc: subprocess.Popen | None = None
        self._token = f"{os.getpid()}-{id(self)}-{time.time_ns()}"
        self._released = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        if self.proc:
            kill_tree(self.proc)

    def _acquire(self) -> None:
        p = inbox.run_dir(self.settings, self.run_id) / LOCK_FILE
        body = json.dumps({"token": self._token, "pid": os.getpid(), "at": inbox._now()}).encode("utf-8")
        for _ in range(2):
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if lock_owner(self.settings, self.run_id) is not None:
                    raise ToolError("conflict", "이 실행은 이미 다른 곳(앱 창 또는 CLI)에서 진행 중입니다",
                                    recovery="그쪽에서 멈춘 뒤 이어서 진행하세요") from None
                p.unlink(missing_ok=True)  # 심장 박동이 끊긴 잠금 — 앞선 실행기가 비정상 종료
                continue
            os.write(fd, body)
            os.close(fd)
            break
        else:
            raise ToolError("conflict", "실행 잠금을 잡지 못했습니다")

        stop_req = p.with_name(STOP_FILE)
        stop_req.unlink(missing_ok=True)

        def beat():
            n = 0
            while not self._released.wait(1.0):
                try:
                    if stop_req.exists():  # 다른 프로세스(앱 창)의 멈춤 요청
                        stop_req.unlink(missing_ok=True)
                        self.stop()
                except Exception:  # noqa: BLE001 — 멈춤 처리가 실패해도 심장 박동은 계속 (잠금이 끊긴 것으로 보이지 않게)
                    pass
                n += 1
                if n % LOCK_BEAT_SEC == 0:
                    try:
                        os.utime(p)
                    except FileNotFoundError:
                        return
                    except OSError:
                        pass

        threading.Thread(target=beat, daemon=True).start()

    def _release(self) -> None:
        self._released.set()
        p = inbox.run_dir(self.settings, self.run_id) / LOCK_FILE
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("token") == self._token:
                p.unlink()
        except (OSError, ValueError):
            pass

    def _set(self, **kw) -> dict:
        return inbox.update_run(self.settings, self.run_id, lambda r: r.update(kw))

    def _turn(self, run: dict, prompt: str) -> dict:
        argv, env = self.agent.command(self.settings, run, prompt, RULES)
        inbox.log(self.settings, self.run_id, "round_start", round=run["round"], agent=self.agent.id, prompt=prompt[:3000])
        feed_stdin = getattr(self.agent, "stdin_prompt", False)
        # stderr 는 파일로 — 파이프로 두면 에이전트가 로그를 많이 쓸 때 양쪽이 서로 기다리며 멈춘다
        err_file = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(argv, cwd=run["target_root"], env=env,
                                     stdin=subprocess.PIPE if feed_stdin else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=err_file)
        if feed_stdin:
            try:
                self.proc.stdin.write(self.agent.stdin_text(run, prompt, RULES).encode("utf-8"))
                self.proc.stdin.close()
            except OSError:
                pass
        result = {}
        texts: list[str] = []
        self.tools_used = 0
        for raw in self.proc.stdout:
            try:
                ev = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            for e in self.agent.parse(ev):
                kind = e.pop("kind")
                if kind == "init":
                    inbox.log(self.settings, self.run_id, "agent_init", **e)
                elif kind == "text":
                    texts.append(e["text"])
                    inbox.log(self.settings, self.run_id, "agent", items=[{"text": e["text"]}])
                elif kind == "tool":
                    self.tools_used += 1
                    inbox.log(self.settings, self.run_id, "agent",
                              items=[{"tool": e.get("tool"), "input": json.dumps(e.get("input"), ensure_ascii=False)[:300]}])
                elif kind == "result":
                    result = {"is_error": not e.get("ok", True), **e}
                    inbox.log(self.settings, self.run_id, "agent_result", subtype=e.get("subtype"), is_error=not e.get("ok", True),
                              cost_usd=e.get("cost_usd"), turns=e.get("turns"), usage=e.get("usage"), result=e.get("text"))
                if e.get("session_id"):
                    run = self._set(session_id=e["session_id"])
                    if e.get("cost_usd"):
                        self._set(cost_usd=round((run.get("cost_usd") or 0) + e["cost_usd"], 4))
            if self._stop.is_set():
                break
        if self._stop.is_set():
            kill_tree(self.proc)
        code = self.proc.wait()
        err_file.seek(0, os.SEEK_END)
        err_file.seek(max(0, err_file.tell() - 6000))
        err = err_file.read().decode("utf-8", "replace")[-1500:]
        err_file.close()
        if code != 0 and not result:
            inbox.log(self.settings, self.run_id, "agent_error", exit_code=code, stderr=err)
        return result

    def _checkpoint(self, run: dict, label: str) -> None:
        try:
            cid = Checkpoints(inbox.run_dir(self.settings, self.run_id), run["target_root"]).snapshot(label)
            if cid:
                inbox.log(self.settings, self.run_id, "checkpoint", id=cid, label=label)
        except (ToolError, OSError) as e:
            inbox.log(self.settings, self.run_id, "checkpoint_error", message=str(e)[:300])

    def _spec_settled(self, run: dict) -> bool:
        """요구사항·합격 기준(또는 오픈소스 모드의 원본 기록)이 승인까지 끝났는지 — 계획 단계로 넘어갈 조건."""
        st = status(self.settings, run)
        if not st:
            return False
        if run["mode"] == "request":
            req = st.get("requirements") or {}
            return bool(req.get("total")) and st["gates"].get(port.GATE_REQ_CASES, False) and not any(
                h["kind"] in ("requirements", "expectation", "case_change") for h in
                port.pending_reviews(project.load(self.settings, run["project_id"])[0], run["port_id"]))
        return st["counts"].get("pending", 0) == 0 and st.get("last_check") is not None

    def run(self) -> dict:
        if not self.exe:
            raise ToolError("not_found", f"{self.agent.name} CLI 를 찾지 못함", recovery=self.agent.status().get("hint"))
        self._acquire()
        try:
            return self._run()
        except BaseException:
            run = inbox.load_run(self.settings, self.run_id)
            if run.get("status") in ("running", "waiting_answer", "waiting_approval", "waiting_plan"):
                self._set(status="stopped")
            raise
        finally:
            if self.proc:
                kill_tree(self.proc)
            self._release()

    def _run(self) -> dict:
        run = self._set(status="running")
        if run["round"] == 0:
            self._checkpoint(run, "시작 전")
        stalls = 0
        finish_tries = 0
        while not self._stop.is_set():
            run = inbox.load_run(self.settings, self.run_id)
            st = status(self.settings, run)
            if st and st.get("finished") and st["finished"].get("valid") and run.get("handoff"):
                return self._set(status="done")
            if st and st["complete"] and not (st.get("finished") or {}).get("valid") and finish_tries < 2:
                finish_tries += 1  # 판정이 바로 무효가 되면(대상 폴더가 계속 바뀜) 헛돌지 않고 라운드로 넘긴다
                conn, _ = project.load(self.settings, run["project_id"])
                port.finish(conn, run["port_id"], "runner")
                inbox.log(self.settings, self.run_id, "finished", gates=st["gates"])
                continue
            waiting = st and port.pending_reviews(project.load(self.settings, run["project_id"])[0], run["port_id"])
            # 승인·계획 대기 중에 사용자가 고칠 점을 보내면 승인하지 않은 채로 그 내용을 반영하는 라운드를 돈다
            if waiting and run["round"] > 0 and "승인" in st["next_step"] and not self._new_messages():
                self._set(status="waiting_approval")
                inbox.log(self.settings, self.run_id, "waiting_approval", items=len(waiting))
                if not self._wait(lambda: self._new_messages() or not port.pending_reviews(
                        project.load(self.settings, run["project_id"])[0], run["port_id"])):
                    break
                run = self._set(status="running")
                continue
            plan = run.get("plan") or {}
            if (run.get("plan_approval", True) and plan.get("steps") and not plan.get("approved") and self._spec_settled(run)
                    and not self._new_messages()):
                self._set(status="waiting_plan")
                inbox.log(self.settings, self.run_id, "waiting_plan", steps=len(plan["steps"]))
                if not self._wait(lambda: self._new_messages() or (inbox.load_run(self.settings, self.run_id).get("plan")
                                                                   or {}).get("approved")):
                    break
                run = self._set(status="running")
                continue
            if inbox.pending(self.settings, self.run_id):
                if not self._settle_orphans():
                    break
                continue
            if run["round"] >= run["max_rounds"]:
                inbox.log(self.settings, self.run_id, "max_rounds", round=run["round"])
                return self._set(status="stopped_max_rounds")
            run = self._set(round=run["round"] + 1)
            prompt = _brief(run, st)
            revising = self._new_messages() and (waiting or ((run.get("plan") or {}).get("steps")
                                                           and not (run.get("plan") or {}).get("approved")))
            if revising:
                prompt += ("\n\n이번 라운드: 사용자가 승인하지 않고 고칠 점을 보냈다. 위 요청대로 요구사항·합격 케이스"
                           "(다시 oss_port_capture) 또는 계획(oss_plan_set)을 고치고 끝낸다. 승인은 사람만 한다.")
            elif run["round"] == 1 and run["mode"] == "request":
                prompt += ("\n\n이번 라운드: 요청을 요구사항(oss_make_requirements)으로 나누고, 요구사항마다 합격 케이스 파일을 "
                           "대상 폴더의 .oss/cases.json 에 써서 oss_port_capture 로 저장한다. 애매한 점은 먼저 묻는다. "
                           "구현은 사용자 승인 뒤에 한다.")
            elif self._spec_settled(run) and not (run.get("plan") or {}).get("steps"):
                prompt += "\n\n이번 라운드: 구현 계획을 oss_plan_set 으로 낸다 (단계·바꿀 파일·실행할 명령). 코드는 아직 바꾸지 않는다."
            elif st and st["complete"] and not run.get("handoff"):
                prompt += "\n\n이번 라운드: 완료 조건을 모두 채웠다. oss_handoff 로 인수인계를 남긴다 (코드는 바꾸지 않는다)."
            self._mark_delivered([(m.get("at"), m.get("text")) for m in run.get("messages") or [] if not m.get("delivered")])
            finish_tries = 0
            before = self._progress_sig(run)
            result = self._turn(run, prompt)
            run = inbox.load_run(self.settings, self.run_id)
            self._checkpoint(run, f"라운드 {run['round']}")
            if self.tools_used == 0 and self._progress_sig(run) == before and not inbox.pending(self.settings, self.run_id):
                stalls += 1
                if stalls >= 2:
                    inbox.log(self.settings, self.run_id, "stalled", round=run["round"])
                    return self._set(status="stopped_max_rounds", stalled=True)
            else:
                stalls = 0
            if result.get("is_error") and result.get("subtype") not in ("error_max_turns",):
                inbox.log(self.settings, self.run_id, "round_error", subtype=result.get("subtype"))
        return self._set(status="stopped")

    def _progress_sig(self, run: dict) -> str:
        """라운드 사이에 무언가 진행됐는지 — 원장 기록 끝, 계획·인수인계, 대상 폴더 스냅샷."""
        try:
            conn, _ = project.load(self.settings, run["project_id"])
            head = conn.execute("SELECT log_head FROM port WHERE id = ?", (run.get("port_id"),)).fetchone() if run.get("port_id") else None
        except ToolError:
            head = None
        cps = Checkpoints(inbox.run_dir(self.settings, self.run_id), run["target_root"]).list()
        return json.dumps([head[0] if head else None, run.get("plan"), run.get("handoff"), cps[0]["id"] if cps else None],
                          ensure_ascii=False, default=str)

    def _settle_orphans(self) -> bool:
        """라운드가 끝났는데 남은 질문·권한 요청 — 묻던 에이전트는 이미 끝났다 (Codex 는 답을 기다리지 않고 턴을 닫기도 한다).

        사용자 답을 기다렸다가 다음 라운드 지시에 실어 보낸다. 허용된 명령은 이 실행에서 기억해 다시 시도하면 통과한다.
        """
        d = inbox.run_dir(self.settings, self.run_id) / "inbox"
        left = inbox.pending(self.settings, self.run_id)
        if not left:
            return True
        self._set(status="waiting_answer")
        inbox.log(self.settings, self.run_id, "waiting_answer", items=len(left))
        if not self._wait(lambda: all((d / f"{q['id']}.answer.json").exists() or not (d / f"{q['id']}.json").exists()
                                      for q in left)):
            return False
        add, cmds = [], []
        for q in left:
            f = d / f"{q['id']}.answer.json"
            try:
                ans = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # 다른 쪽(살아 있던 에이전트)이 이미 받아 갔다
            f.unlink(missing_ok=True)
            (d / f"{q['id']}.json").unlink(missing_ok=True)
            inbox.log(self.settings, self.run_id, "answer", id=q["id"], answer=ans)
            if q.get("kind") == "question":
                text = ans.get("text") or ans.get("choice") or ""
                add.append({"text": f"앞서 한 질문에 대한 답 — 질문: {q.get('question', '')[:1000]} / 답: {text[:3000]}",
                            "at": inbox._now(), "delivered": False, "answer_to": q["id"]})
            else:
                inp = q.get("input") or {}
                if ans.get("allow") and str(q.get("tool", "")).startswith("mcp:"):
                    cmds.extend(inp.get("commands") or ([inp["command"]] if inp.get("command") else []))
                verdict = "허용 — 같은 작업을 다시 시도한다" if ans.get("allow") else "거절 — 다른 방법을 찾는다"
                add.append({"text": f"앞서 한 권한 요청에 대한 답: {q.get('summary', '')[:500]} → {verdict}",
                            "at": inbox._now(), "delivered": False, "answer_to": q["id"]})

        def put(r):
            r.setdefault("messages", []).extend(add)
            r["approved_cmds"] = list(dict.fromkeys((r.get("approved_cmds") or []) + cmds))
            r["status"] = "running"

        inbox.update_run(self.settings, self.run_id, put)
        return True

    def _new_messages(self) -> bool:
        return any(not m.get("delivered") for m in inbox.load_run(self.settings, self.run_id).get("messages") or [])

    def _mark_delivered(self, sent: list[tuple]) -> None:
        """이번 지시에 실은 메시지만 전달됨으로 — 지시를 만든 뒤 들어온 메시지는 다음 라운드로 넘긴다."""
        if not sent:
            return

        def mark(r):
            for m in r.get("messages") or []:
                if not m.get("delivered") and (m.get("at"), m.get("text")) in sent:
                    m["delivered"] = True

        inbox.update_run(self.settings, self.run_id, mark)

    def _wait(self, ready) -> bool:
        while not self._stop.wait(1.0):
            if ready():
                return True
        return False

