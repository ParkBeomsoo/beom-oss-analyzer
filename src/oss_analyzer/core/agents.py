"""에이전트 연결부 — Claude Code · Codex · Gemini CLI 를 같은 모양으로 다룬다.

각 연결부가 하는 일: 설치·로그인 확인, 한 라운드 실행 명령 만들기(이어서 실행 포함), 출력 이벤트를 공통 형식으로 바꾸기.
공통 이벤트: {"kind": "init"|"text"|"tool"|"result"|"error", ...}

권한 처리 차이:
- Claude Code: --permission-prompt-tool 로 작업마다 우리 규칙(inbox.py)이 허용·물어보기·거절을 정한다.
- Codex: 헤드리스 실행에서 작업별 승인 연결이 없어 샌드박스로 막는다 — 대상 폴더 안에서만 쓰기, 웹은 규칙이 허용일 때만.
- Gemini CLI: 헤드리스에서 물어볼 작업은 거절된다 — 샌드박스 + 허용 목록. (실험적)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MCP_NAME = "oss-analyzer"
_EFFORT_KO = {"minimal": "최소", "low": "낮음", "medium": "보통", "high": "높음", "xhigh": "매우 높음", "max": "최대", "ultra": "울트라"}
MCP_STARTUP_SEC = 60
MCP_TOOL_SEC = 24 * 3600  # oss_person_ask·oss_action_approve 는 사람이 답할 때까지 기다린다


def _mcp_server(settings, run: dict) -> dict:
    return {"command": sys.executable, "args": ["-m", "oss_analyzer.main"],
            "env": {"OSS_ANALYZER_RUN": run["id"], "OSS_ANALYZER_HOME": str(settings.home)}}


def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _run_quiet(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


class Agent:
    id = ""
    name = ""
    permission_hook = False
    note = ""
    # 지시문을 표준 입력으로 넘긴다 — Windows 의 .cmd 실행 파일(npm 설치)은 여러 줄 인자를 첫 줄에서 자른다
    stdin_prompt = False
    # 'API 키' 인증일 때 읽는 환경 변수 — 로그인 대신 키로 과금될 뿐 할 수 있는 일은 같다
    api_key_env: tuple[str, ...] = ()

    def __init__(self, exe=None):
        self.exe = exe or self.find()

    def find(self) -> str | None:
        return None

    def status(self) -> dict:
        return {"id": self.id, "name": self.name, "ready": False, "exe": self.exe}

    def base(self) -> list[str]:
        return self.exe if isinstance(self.exe, list) else [self.exe]

    def command(self, settings, run: dict, prompt: str, rules: str) -> tuple[list[str], dict]:
        raise NotImplementedError

    def parse(self, ev: dict) -> list[dict]:
        return []

    def models(self) -> list[dict]:
        """고를 수 있는 모델 — 첫 항목(id 빈 값)은 에이전트 기본값."""
        return [{"id": "", "label": "기본값"}]

    def efforts(self) -> dict[str, list[dict]]:
        """모델별로 고를 수 있는 생각 깊이(노력). 키 "" 는 기본 모델. 비어 있으면 이 에이전트는 설정 없음."""
        return {}

    def env(self, run: dict) -> dict:
        return dict(os.environ)


# ---------------------------------------------------------------- Claude Code

class ClaudeCode(Agent):
    id, name, permission_hook = "claude", "Claude Code", True
    api_key_env = ("ANTHROPIC_API_KEY",)
    TOOL_PREFIX = f"mcp__{MCP_NAME}__"

    def find(self) -> str | None:
        from oss_analyzer.core import runner

        return runner.find_claude()

    def status(self) -> dict:
        from oss_analyzer.core import runner

        st = runner.agent_status()
        return {"id": self.id, "name": self.name, "ready": st["ready"], "exe": st.get("claude"),
                "detail": st.get("auth_method"), "hint": st.get("hint"), "login": st.get("login_command")}

    stdin_prompt = True

    def command(self, settings, run, prompt, rules):
        from oss_analyzer.core import inbox

        # 여러 줄·따옴표 인자는 npm 설치본(claude.cmd)에서 깨진다 — 규칙·MCP 설정은 파일로, 지시는 표준 입력으로
        d = inbox.run_dir(settings, run["id"]) / "agent"  # 라운드마다 다시 만드는 설정 — 기록과 분리
        d.mkdir(parents=True, exist_ok=True)
        mcp_file, rules_file = d / "claude-mcp.json", d / "claude-rules.md"
        mcp_file.write_text(json.dumps({"mcpServers": {MCP_NAME: _mcp_server(settings, run)}}, ensure_ascii=False),
                            encoding="utf-8")
        rules_file.write_text(rules, encoding="utf-8")
        argv = [*self.base(), "-p", "--output-format", "stream-json", "--verbose",
                "--mcp-config", str(mcp_file), "--strict-mcp-config",
                "--permission-prompt-tool", f"{self.TOOL_PREFIX}oss_action_approve",
                # 읽기도 규칙을 거친다 (대상 폴더 안은 허용, 밖은 물어보기) — 미리 허용하면 툴킷 원장·질문함까지 읽힌다
                "--allowedTools", ",".join(["TodoWrite", f"{self.TOOL_PREFIX}*"]),
                "--disallowedTools", ",".join(["AskUserQuestion", f"{self.TOOL_PREFIX}oss_port_approve"]),
                "--append-system-prompt-file", str(rules_file), "--max-turns", str(run.get("max_turns", 60))]
        if run.get("model"):
            argv += ["--model", run["model"]]
        if run.get("effort"):
            argv += ["--effort", run["effort"]]
        if run.get("session_id"):
            argv += ["--resume", run["session_id"]]
        return argv, self.env(run)

    def stdin_text(self, run: dict, prompt: str, rules: str) -> str:
        return prompt

    def models(self) -> list[dict]:
        # 별칭은 Claude Code 가 그때의 최신 모델로 풀어 준다
        return [{"id": "", "label": "기본값", "note": "계정 설정을 따름"}, {"id": "opus", "label": "Opus", "note": "가장 강함"},
                {"id": "sonnet", "label": "Sonnet", "note": "균형"}, {"id": "haiku", "label": "Haiku", "note": "빠름"}]

    def efforts(self) -> dict[str, list[dict]]:
        levels = [{"id": "low", "label": "낮음", "desc": "빠르게"}, {"id": "medium", "label": "보통", "desc": "일상 작업"},
                  {"id": "high", "label": "높음", "desc": "복잡한 작업"}, {"id": "xhigh", "label": "매우 높음", "desc": "더 깊게"},
                  {"id": "max", "label": "최대", "desc": "가장 어려운 문제"}]
        return {m["id"]: levels for m in self.models()}

    def env(self, run):
        env = dict(os.environ)
        if run.get("auth") != "api_key":
            env.pop("ANTHROPIC_API_KEY", None)  # 키가 있으면 -p 는 항상 키를 쓴다 — 구독 로그인으로 돌게 뺀다
        env.setdefault("MCP_TIMEOUT", str(MCP_STARTUP_SEC * 1000))
        env.setdefault("MCP_TOOL_TIMEOUT", str(MCP_TOOL_SEC * 1000))
        return env

    def parse(self, ev):
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            return [{"kind": "init", "session_id": ev.get("session_id"),
                     "mcp": {s.get("name"): s.get("status") for s in ev.get("mcp_servers") or []}}]
        if t == "assistant":
            out = []
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    out.append({"kind": "text", "text": b["text"].strip()[:4000]})
                elif b.get("type") == "tool_use":
                    out.append({"kind": "tool", "tool": b.get("name"), "input": b.get("input")})
            return out
        if t == "result":
            return [{"kind": "result", "ok": not ev.get("is_error"), "subtype": ev.get("subtype"),
                     "session_id": ev.get("session_id"), "cost_usd": ev.get("total_cost_usd"), "turns": ev.get("num_turns"),
                     "text": (ev.get("result") or "")[:2000]}]
        return []


# ---------------------------------------------------------------- Codex

class Codex(Agent):
    id, name = "codex", "Codex"
    api_key_env = ("CODEX_API_KEY", "OPENAI_API_KEY")
    stdin_prompt = True
    note = "작업별 승인 대신 샌드박스로 막습니다 (대상 폴더 안에서만 쓰기, 웹은 규칙이 허용일 때만)."

    def find(self) -> str | None:
        return _which("codex") or next((str(p) for p in (Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd",)
                                        if p.is_file()), None)

    def status(self) -> dict:
        if not self.exe:
            return {"id": self.id, "name": self.name, "ready": False, "exe": None,
                    "hint": "Codex CLI 가 없음 — npm install -g @openai/codex 후 `codex login`"}
        proc = _run_quiet([*self.base(), "login", "status"])
        ok = proc is not None and proc.returncode == 0
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()[:200] if proc else ""
        return {"id": self.id, "name": self.name, "ready": ok, "exe": self.exe, "detail": text,
                "hint": None if ok else "로그인 필요 — 터미널에서 `codex login`", "login": [*self.base(), "login"]}

    def command(self, settings, run, prompt, rules):
        srv = _mcp_server(settings, run)
        pol = run.get("policy") or {}
        cfg = [f'mcp_servers.{MCP_NAME}.command={json.dumps(srv["command"])}',
               f'mcp_servers.{MCP_NAME}.args={json.dumps(srv["args"])}',
               "mcp_servers.%s.env={%s}" % (MCP_NAME, ", ".join(f'{k}={json.dumps(v)}' for k, v in srv["env"].items())),
               # "auto" 는 도구마다 승인을 물어보는데 approval_policy=never 라 전부 거절된다 — 우리 도구는 항상 허용
               f"mcp_servers.{MCP_NAME}.default_tools_approval_mode=\"approve\"",
               # 서버 기동은 mcp SDK import 만 수 초 (기본 10초를 넘길 때가 있음), 사람에게 묻는 도구는 답을 기다린다
               f"mcp_servers.{MCP_NAME}.startup_timeout_sec={MCP_STARTUP_SEC}",
               f"mcp_servers.{MCP_NAME}.tool_timeout_sec={MCP_TOOL_SEC}",
               # 기본("connection")은 도구 목록이 오기 전에 작업을 시작해 버린다 — 목록까지 기다리고, 못 붙으면 실패로 끝낸다
               f'mcp_servers.{MCP_NAME}.startup_readiness="catalog"', f"mcp_servers.{MCP_NAME}.required=true",
               'sandbox_mode="workspace-write"', 'approval_policy="never"',
               f"sandbox_workspace_write.network_access={'true' if pol.get('web') == 'allow' else 'false'}"]
        if sys.platform == "win32":
            # Windows 는 샌드박스 방식을 정하지 않으면 쓰기가 모두 막힌다 — 관리자 설치가 필요 없는 방식
            cfg.append('windows.sandbox="unelevated"')
        # exec resume 는 -s·-C 를 받지 않는다 (codex 0.167) — 샌드박스는 설정 값으로, 작업 폴더는 프로세스 cwd 로
        flags = ["--json", "--skip-git-repo-check"]
        for c in cfg:
            flags += ["-c", c]
        if run.get("model"):
            flags += ["-m", run["model"]]
        if run.get("effort"):
            flags += ["-c", f'model_reasoning_effort="{run["effort"]}"']
        if run.get("session_id"):
            return [*self.base(), "exec", "resume", *flags, run["session_id"], "-"], self.env(run)
        return [*self.base(), "exec", *flags, "-"], self.env(run)

    def stdin_text(self, run: dict, prompt: str, rules: str) -> str:
        return prompt if run.get("session_id") else f"{rules}\n\n{prompt}"

    def models(self) -> list[dict]:
        """Codex 가 로그인 계정 기준으로 받아 둔 모델 목록(~/.codex/models_cache.json) — 없으면 기본값만."""
        out = [{"id": "", "label": "기본값", "note": "Codex 설정을 따름"}]
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        try:
            data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return out
        rows = [m for m in data.get("models") or [] if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
        for m in sorted(rows, key=lambda m: m.get("priority") or 999)[:12]:
            out.append({"id": m["slug"], "label": m.get("display_name") or m["slug"], "default_effort": m.get("default_reasoning_level"),
                        "levels": [{"id": x.get("effort"), "label": _EFFORT_KO.get(x.get("effort"), x.get("effort")), "desc": x.get("description", "")}
                                   for x in m.get("supported_reasoning_levels") or [] if x.get("effort")]})
        return out

    def efforts(self) -> dict[str, list[dict]]:
        ms = self.models()
        out = {m["id"]: m.get("levels") or [] for m in ms if m["id"]}
        if len(ms) > 1:  # 기본 모델은 Codex 설정의 모델 — 목록 첫 모델의 단계로 안내
            out[""] = ms[1].get("levels") or []
        return out

    def env(self, run):
        env = dict(os.environ)
        if run.get("auth") != "api_key":
            env.pop("CODEX_API_KEY", None)
            env.pop("OPENAI_API_KEY", None)
        return env

    def parse(self, ev):
        t = ev.get("type")
        if t == "thread.started":
            return [{"kind": "init", "session_id": ev.get("thread_id")}]
        if t in ("item.completed", "item.started"):
            item = ev.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if t == "item.completed" and it in ("agent_message", "assistant_message") and item.get("text"):
                return [{"kind": "text", "text": item["text"][:4000]}]
            if t == "item.started" and it == "command_execution":
                return [{"kind": "tool", "tool": "Bash", "input": {"command": item.get("command")}}]
            if t == "item.completed" and it in ("mcp_tool_call", "file_change"):
                name = item.get("tool") or item.get("name") or it
                out = [{"kind": "tool", "tool": f"mcp__{MCP_NAME}__{name}" if it == "mcp_tool_call" else "Edit",
                        "input": item.get("arguments") or {"changes": item.get("changes")}}]
                err = item.get("error")
                if item.get("status") == "failed" and err:
                    out.append({"kind": "text", "text": f"[도구 실패] {name}: {err.get('message') if isinstance(err, dict) else err}"[:2000]})
                return out
        if t == "turn.completed":
            return [{"kind": "result", "ok": True, "usage": ev.get("usage")}]
        if t in ("turn.failed", "error"):
            msg = (ev.get("error") or {}).get("message") if isinstance(ev.get("error"), dict) else ev.get("message")
            return [{"kind": "result", "ok": False, "text": str(msg)[:2000]}]
        return []


# ---------------------------------------------------------------- Gemini CLI (실험적)

class GeminiCLI(Agent):
    id, name = "gemini", "Gemini CLI (실험적)"
    api_key_env = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    stdin_prompt = True
    note = "헤드리스에서는 물어볼 작업이 거절됩니다. MCP 는 실행마다 임시 설정 파일로 붙입니다. 로그인 확인 명령이 없어 실행해 봐야 압니다."

    def find(self) -> str | None:
        return _which("gemini")

    def status(self) -> dict:
        return {"id": self.id, "name": self.name, "ready": bool(self.exe), "exe": self.exe,
                "hint": None if self.exe else "Gemini CLI 가 없음 — npm install -g @google/gemini-cli",
                "detail": "로그인 상태는 실행할 때 확인됩니다"}

    def command(self, settings, run, prompt, rules):
        from oss_analyzer.core import inbox

        d = inbox.run_dir(settings, run["id"]) / "agent"
        d.mkdir(parents=True, exist_ok=True)
        conf = d / "gemini-settings.json"
        conf.write_text(json.dumps({"mcpServers": {MCP_NAME: {**_mcp_server(settings, run), "trust": True}}},
                                   ensure_ascii=False), encoding="utf-8")
        argv = [*self.base(), "-p", "표준 입력의 지시를 따른다.", "-o", "stream-json", "--approval-mode", "auto_edit",
                "--allowed-mcp-server-names", MCP_NAME]
        if run.get("model"):
            argv += ["-m", run["model"]]
        if run.get("session_id"):
            argv += ["-r", run["session_id"]]
        env = dict(os.environ, GEMINI_CLI_SYSTEM_SETTINGS_PATH=str(conf))
        return argv, env

    def stdin_text(self, run: dict, prompt: str, rules: str) -> str:
        return f"{rules}\n\n{prompt}"

    def models(self) -> list[dict]:
        return [{"id": "", "label": "기본값"}, {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
                {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"}]

    def parse(self, ev):
        t = ev.get("type")
        if t == "init":
            return [{"kind": "init", "session_id": ev.get("session_id")}]
        if t == "message" and ev.get("role", "assistant") == "assistant" and ev.get("content"):
            return [{"kind": "text", "text": str(ev["content"])[:4000]}]
        if t == "tool_use":
            return [{"kind": "tool", "tool": ev.get("tool_name") or ev.get("name"), "input": ev.get("parameters") or ev.get("args")}]
        if t == "result":
            return [{"kind": "result", "ok": ev.get("status", "success") == "success", "usage": ev.get("stats")}]
        if t == "error":
            return [{"kind": "result", "ok": False, "text": str(ev.get("message"))[:2000]}]
        return []


AGENTS = {a.id: a for a in (ClaudeCode, Codex, GeminiCLI)}


def get(agent_id: str | None, exe=None) -> Agent:
    cls = AGENTS.get(agent_id or "claude")
    if cls is None:
        raise ValueError(f"모르는 에이전트: {agent_id}")
    return cls(exe)


def statuses(check: bool = True) -> list[dict]:
    """에이전트 목록. check=False 면 설치 여부만 빠르게 (로그인 확인은 몇 초 걸린다) — ready 는 None(확인 중)."""
    out = []
    for cls in AGENTS.values():
        if not check:
            a = cls()
            out.append({"id": cls.id, "name": cls.name, "ready": None, "exe": a.exe, "permission_hook": cls.permission_hook,
                        "note": cls.note, "models": a.models(), "efforts": a.efforts()})
            continue
        try:
            a = cls()
            out.append({**a.status(), "permission_hook": cls.permission_hook, "note": cls.note, "models": a.models(),
                        "efforts": a.efforts()})
        except Exception as e:  # noqa: BLE001 — 한 에이전트 확인 실패가 목록을 막지 않게
            out.append({"id": cls.id, "name": cls.name, "ready": False, "hint": f"{type(e).__name__}: {e}"})
    return out
