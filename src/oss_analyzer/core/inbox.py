"""실행(run) 상태·질문함·승인 규칙 — 자동 실행기와 데스크톱 앱이 함께 쓴다.

실행 폴더: <home>/runs/<run_id>/
  run.json       모드·프로젝트·원장·상태·라운드·규칙
  events.jsonl   실행 로그 (에이전트 이벤트·검증 결과·단계 변화)
  inbox/<id>.json        에이전트가 기다리는 질문·권한 요청 (앱이 카드로 보여 줌)
  inbox/<id>.answer.json 사람의 답 (앱이 씀)

권한 요청은 종류별 규칙으로 먼저 판단한다 — allow / ask / refuse. ask 만 사람에게 간다.
에이전트는 규칙을 더 엄격하게만 바꿀 수 있다. 실행 폴더는 대상 저장소 밖이라 에이전트의 파일 쓰기 규칙으로는 닿지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings

RUN_SCHEMA = 1  # run.json 형식 — 바뀌면 load_run 에서 올린다
DECISIONS = ("allow", "ask", "refuse")
KINDS = {
    "edit": "대상 폴더 안 파일 수정",
    "build": "정해진 빌드·테스트 명령",
    "commands": "그 밖의 명령 실행",
    "web": "웹 접속",
    "outside": "대상 폴더 밖 파일 읽기·쓰기",
    "publish": "push·배포·패키지 게시",
    "delete": "대상 폴더 밖 삭제",
    "system": "시스템·설정 변경 (툴킷 원장·질문함 포함)",
    "other": "그 밖의 도구",
}
DEFAULT_POLICY = {"edit": "allow", "build": "allow", "commands": "ask", "web": "ask", "outside": "ask",
                  "publish": "refuse", "delete": "refuse", "system": "refuse", "other": "ask"}
STRICTNESS = {"allow": 0, "ask": 1, "refuse": 2}
# 권한 모드 — 대부분은 셋 중 하나로 충분하다. 세부(종류별) 규칙은 "직접 설정"으로.
_NEVER = {"publish": "refuse", "delete": "refuse", "system": "refuse"}
PERMISSION_MODES = {
    "ask": {"label": "모두 물어보기", "desc": "파일 수정·명령 실행마다 확인합니다",
            "policy": {"edit": "ask", "build": "ask", "commands": "ask", "web": "ask", "outside": "ask", "other": "ask", **_NEVER}},
    "edits": {"label": "편집 자동 허용", "desc": "대상 폴더 안 수정과 빌드·테스트는 바로, 그 밖의 명령·웹은 확인합니다",
              "policy": dict(DEFAULT_POLICY)},
    "auto": {"label": "자동", "desc": "명령까지 묻지 않고 바로 실행합니다 (웹·폴더 밖 파일만 확인). 믿을 수 있는 작업에만",
             "policy": {"edit": "allow", "build": "allow", "commands": "allow", "web": "ask", "outside": "ask", "other": "ask",
                        **_NEVER}},
}


def mode_of(policy: dict) -> str:
    """규칙이 어느 모드와 같은지 — 어느 것과도 다르면 custom."""
    for k, m in PERMISSION_MODES.items():
        if all(policy.get(kind) == v for kind, v in m["policy"].items()):
            return k
    return "custom"


# 정확히 이 줄만 '빌드'로 바로 허용 — 덧붙인 인자는 실행기를 바꿀 수 있다 (go test -exec, yarn dlx, pytest -p 등).
# 그 밖의 줄은 '그 밖의 명령'으로 규칙에 따라 묻는다.
BUILD_COMMANDS = re.compile(
    r"^(npm (ci|install|test|run (build|test|lint))|pnpm (install|test|build)|yarn (install|test|build)|"
    r"cargo (build|test|check|fmt|clippy)|go (build|test|vet)( \./\.\.\.)?|mvn( -q)? (test|package|verify)|gradlew? (test|build)|"
    r"\./gradlew (test|build)|pytest|python -m pytest|py -m pytest|uv run pytest|pip install -e \.|dotnet (build|test))\s*$")
PUBLISH = re.compile(r"\b(git\s+push|npm\s+publish|cargo\s+publish|twine\s+upload|docker\s+push|gh\s+release|kubectl\s+apply|helm\s+(install|upgrade))\b")
SYSTEM = re.compile(r"\b(setx|reg(\.exe)?\s+add|sudo|chmod\s+-R\s+/|systemctl|launchctl|netsh|Set-ExecutionPolicy|OSS_ANALYZER_)\b", re.I)
CHAINING = re.compile(r"[\n\r&|;`<>]|\$\(|\$\{")
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def runs_dir(settings: Settings) -> Path:
    d = settings.home / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_dir(settings: Settings, run_id: str) -> Path:
    if not re.fullmatch(r"run_[0-9a-f]{12}", run_id):
        raise ToolError("invalid_request", f"실행 id 형식이 아님: {run_id}")
    return runs_dir(settings) / run_id


def new_run(settings: Settings, meta: dict) -> dict:
    run_id = "run_" + secrets.token_hex(6)
    d = run_dir(settings, run_id)
    (d / "inbox").mkdir(parents=True)
    run = {"id": run_id, "schema": RUN_SCHEMA, "created_at": _now(), "status": "created", "round": 0,
           "policy": {**DEFAULT_POLICY, **(meta.pop("policy", None) or {})}, **meta}
    save_run(settings, run)
    return run


def _retry(fn, tries: int = 40, wait: float = 0.025):
    """Windows 는 다른 프로세스가 읽는 중인 파일을 바꾸거나(replace) 바뀌는 중인 파일을 읽으면 실패한다 — 잠깐 뒤 다시."""
    for i in range(tries):
        try:
            return fn()
        except (PermissionError, json.JSONDecodeError):
            if i == tries - 1:
                raise
            time.sleep(wait)


def load_run(settings: Settings, run_id: str) -> dict:
    f = run_dir(settings, run_id) / "run.json"
    if not f.exists():
        raise ToolError("not_found", f"실행 없음: {run_id}")
    return _retry(lambda: json.loads(f.read_text(encoding="utf-8")))


def save_run(settings: Settings, run: dict) -> None:
    f = run_dir(settings, run["id"]) / "run.json"
    tmp = f.with_name(f"run.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _retry(lambda: os.replace(tmp, f))
    finally:
        tmp.unlink(missing_ok=True)


class _RunLock:
    """run.json 을 읽고-고치고-쓰는 동안 다른 프로세스(앱·실행기·MCP 서버)가 끼어들지 않게 하는 짧은 잠금."""

    def __init__(self, settings: Settings, run_id: str, timeout: float = 10.0, stale: float = 15.0):
        self.path = run_dir(settings, run_id) / "run.json.lock"
        self.timeout, self.stale = timeout, stale

    def __enter__(self):
        end = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.path.unlink(missing_ok=True)  # 잡은 쪽이 죽었다
                        continue
                except OSError:
                    continue
                if time.monotonic() > end:
                    raise ToolError("timeout", "실행 상태 파일 잠금을 얻지 못함") from None
                time.sleep(0.02)
            except PermissionError:
                if time.monotonic() > end:
                    raise ToolError("timeout", "실행 상태 파일 잠금을 얻지 못함") from None
                time.sleep(0.02)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def update_run(settings: Settings, run_id: str, fn) -> dict:
    """run.json 을 잠근 채로 읽어 fn(run) 으로 고치고 저장한다 — 여러 곳에서 동시에 고쳐도 서로의 변경을 잃지 않는다."""
    with _RunLock(settings, run_id):
        run = load_run(settings, run_id)
        fn(run)
        save_run(settings, run)
        return run


def list_runs(settings: Settings) -> list[dict]:
    out = []
    for d in sorted(runs_dir(settings).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "run.json"
        if f.exists():
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def log(settings: Settings, run_id: str, kind: str, **data) -> None:
    with open(run_dir(settings, run_id) / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _now(), "kind": kind, **data}, ensure_ascii=False) + "\n")


def events(settings: Settings, run_id: str, offset: int = 0) -> tuple[list[dict], int]:
    f = run_dir(settings, run_id) / "events.jsonl"
    if not f.exists():
        return [], offset
    with open(f, encoding="utf-8") as fh:
        fh.seek(offset)
        data = fh.read()
        end = fh.tell()
    out = []
    for line in data.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out, end


def tighten(policy: dict, changes: dict) -> dict:
    """에이전트가 바꾸는 규칙 — 더 엄격한 쪽만 받아들인다."""
    out = dict(policy)
    for k, v in changes.items():
        if k in KINDS and v in DECISIONS and STRICTNESS[v] > STRICTNESS[out.get(k, "ask")]:
            out[k] = v
    return out


def _inside(path: str | None, root: Path) -> bool:
    """path 가 root 안(또는 root 자체)인지. 문자열 앞부분 비교가 아니라 경로 단위 — app 과 app2 를 구분한다."""
    if not path:
        return False
    try:
        p = Path(path)
        p = (root / p) if not p.is_absolute() else p
        a, b = os.path.normcase(str(p.resolve())), os.path.normcase(str(Path(root).resolve()))
        return a == b or a.startswith(b.rstrip("\\/") + os.sep)
    except (OSError, ValueError):
        return False


def classify(tool: str, tool_input: dict, target_root: Path, protected: list[Path]) -> str:
    """권한 요청을 종류로 나눈다. protected: 툴킷 홈·실행 폴더 — 여기 닿으면 system."""
    path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    if path and any(_inside(path, p) for p in protected):
        return "system"
    if tool in FILE_TOOLS:
        return "edit" if _inside(path, target_root) else "outside"
    if tool in READ_TOOLS:
        return "edit" if (not path or _inside(path, target_root)) else "outside"
    if tool in ("WebFetch", "WebSearch"):
        return "web"
    if tool in ("Bash", "PowerShell"):
        cmd = str(tool_input.get("command", "")).strip()
        if _touches(cmd, protected) or SYSTEM.search(cmd):
            return "system"
        if PUBLISH.search(cmd):
            return "publish"
        if re.search(r"\brm\s+-[a-zA-Z]*r|\brmdir\b|\bRemove-Item\b|\bdel\s+/s\b", cmd, re.I) and not _cmd_inside(cmd, target_root):
            return "delete"
        # 이어 붙이기·치환·리다이렉트가 있으면 빌드 명령 뒤에 다른 명령이 숨을 수 있다 — 물어보는 쪽으로
        return "build" if BUILD_COMMANDS.match(cmd) and not CHAINING.search(cmd) else "commands"
    if tool.startswith("mcp__oss-analyzer__"):
        return "other" if tool.endswith(("port_approve",)) else "edit"
    return "other"


def _touches(cmd: str, protected: list[Path]) -> bool:
    """명령이 툴킷 홈(원장·질문함)을 건드리는지 — 슬래시 방향·~·환경 변수 표기까지."""
    low = cmd.lower().replace("\\", "/")
    if ".oss-analyzer" in low or "oss_analyzer_home" in low:
        return True
    for p in protected:
        s = str(p).lower().replace("\\", "/")
        if s in low:
            return True
    return False


def _cmd_inside(cmd: str, root: Path) -> bool:
    try:
        parts = shlex.split(cmd, posix=os.name != "nt")
    except ValueError:
        return False
    paths = [p for p in parts[1:] if not p.startswith("-")]
    return bool(paths) and all(_inside(p, root) for p in paths)


# ---------------------------------------------------------------- 질문함

def ask(settings: Settings, run_id: str, kind: str, payload: dict, timeout: float = 24 * 3600, poll: float = 0.5) -> dict:
    """사람에게 묻고 답을 기다린다. kind: permission | question. 답이 없으면 timeout 뒤 거절로 본다."""
    rid = secrets.token_hex(6)
    d = run_dir(settings, run_id) / "inbox"
    req = {"id": rid, "kind": kind, "at": _now(), **payload}
    (d / f"{rid}.json").write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    log(settings, run_id, "ask", id=rid, ask_kind=kind, summary=payload.get("summary") or payload.get("question"))
    ans_f = d / f"{rid}.answer.json"
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if ans_f.exists():
            try:
                ans = json.loads(ans_f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(poll)
                continue
            (d / f"{rid}.json").unlink(missing_ok=True)
            ans_f.unlink(missing_ok=True)
            log(settings, run_id, "answer", id=rid, answer=ans)
            return ans
        time.sleep(poll)
    (d / f"{rid}.json").unlink(missing_ok=True)
    log(settings, run_id, "answer", id=rid, answer={"timeout": True})
    return {"timeout": True}


def pending(settings: Settings, run_id: str) -> list[dict]:
    d = run_dir(settings, run_id) / "inbox"
    out = []
    for f in sorted(d.glob("*.json")):
        if f.name.endswith(".answer.json"):
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def answer(settings: Settings, run_id: str, request_id: str, data: dict) -> None:
    if not re.fullmatch(r"[0-9a-f]{12}", request_id):
        raise ToolError("invalid_request", "요청 id 형식이 아님")
    d = run_dir(settings, run_id) / "inbox"
    if not (d / f"{request_id}.json").exists():
        raise ToolError("not_found", "이미 답했거나 없는 요청")
    tmp = d / f"{request_id}.answer.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / f"{request_id}.answer.json")


# ---------------------------------------------------------------- MCP 서버가 대신 실행하는 명령

def gate_commands(settings: Settings, commands: dict[str, str | None]) -> None:
    """자동 실행 중에 에이전트가 MCP 도구(캡처·검증·커버리지)로 넘긴 명령을 규칙대로 거른다.

    이 명령들은 MCP 서버 프로세스에서 돌아 에이전트 쪽 샌드박스·권한 확인을 거치지 않는다. 그래서 여기서 같은 규칙
    (build 허용 · 그 밖은 물어보기 · 시스템/게시는 거절)을 적용하고, 사람이 허용한 명령 문자열은 이 실행 동안 기억한다.
    자동 실행 밖(사람이 직접 CLI·대화로 돌릴 때)은 대상이 아니다.
    """
    run_id = os.environ.get("OSS_ANALYZER_RUN")
    if not run_id:
        return
    run = load_run(settings, run_id)
    todo = []
    for field, cmd in commands.items():
        if not cmd or cmd in (run.get("approved_cmds") or []):
            continue
        kind = classify("Bash", {"command": cmd}, Path(run["target_root"]), [settings.home])
        decision = run["policy"].get(kind, "ask")
        log(settings, run_id, "permission", tool=f"mcp:{field}", permission_kind=kind, decision=decision)
        if decision != "allow" and decision != "ask":
            raise ToolError("denied", f"{field} 명령이 규칙상 허용되지 않음 ({KINDS.get(kind, kind)}): {cmd[:200]}",
                            recovery="다른 방법을 쓰거나 oss_person_ask 로 사용자에게 이유와 함께 묻는다")
        todo.append((field, cmd, kind, decision))
    asks = [t for t in todo if t[3] == "ask"]
    if asks:  # 한 번의 도구 호출에 들어 있는 명령은 카드 하나로 묻는다
        cmds = [c for _, c, _, _ in asks]
        ans = ask(settings, run_id, "permission", {
            "summary": "검증 도구가 실행할 명령: " + " / ".join(f"{f}: {c[:200]}" for f, c, _, _ in asks),
            "tool": "mcp:" + ",".join(f for f, _, _, _ in asks), "input": {"command": cmds[0], "commands": cmds},
            "permission_kind": asks[0][2]})
        if not ans.get("allow"):
            raise ToolError("denied", "사용자가 명령 실행을 거절함: " + " / ".join(c[:120] for c in cmds),
                            recovery="다른 방법을 쓰거나 oss_person_ask 로 사용자에게 이유와 함께 묻는다")
    new = [c for _, c, _, d in todo if d == "ask"]  # 규칙이 허용한 명령은 기억하지 않는다 — 나중에 규칙을 조이면 다시 거른다
    if new:
        update_run(settings, run_id, lambda r: r.update(approved_cmds=list(dict.fromkeys((r.get("approved_cmds") or []) + new))))
