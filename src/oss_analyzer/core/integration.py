"""에이전트 연동 상태 — CLI 가 PATH 에 있는지, 스킬 사본이 지금 설치본과 같은지, MCP 등록이 이 설치를 가리키는지.

setup/doctor 원칙:
- 경로는 문서에 적지 않고 실행 중인 설치에서 구한다 (사람마다 설치 위치가 다르다).
- 스킬은 패키지에 함께 들어 있고(oss_analyzer/skills), 설치한 사본에는 소유 표시(버전·지문)를 남긴다.
  표시가 없는 같은 이름의 스킬은 사용자 것으로 보고 덮어쓰지 않는다. 지문이 다르면 outdated.
- MCP 등록이 다른 설치(다른 Python)를 가리키면 foreign — 자동으로 바꾸지 않는다.
- 범위: user(이 사용자의 모든 프로젝트) 또는 project(한 프로젝트에서만 — 그 프로젝트의 .claude/skills, .mcp.json).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from oss_analyzer import __version__

SKILL_NAME = "oss-analyze"
MCP_NAME = "oss-analyzer"
MANIFEST = ".oss-analyzer-skill.json"
SCOPES = ("user", "project")


def bundled_skill() -> Path:
    return Path(str(resources.files("oss_analyzer") / "skills" / SKILL_NAME))


def skill_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != MANIFEST)


def skill_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in skill_files(root):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _check_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"scope 는 {SCOPES} 중 하나")


def skill_destination(scope: str = "user", project_dir: Path | None = None) -> Path:
    _check_scope(scope)
    if scope == "project":
        return Path(project_dir or Path.cwd()).resolve() / ".claude" / "skills" / SKILL_NAME
    return claude_home() / "skills" / SKILL_NAME


def skill_status(dst: Path | None = None) -> dict:
    dst = dst or skill_destination()
    want = skill_digest(bundled_skill())
    if dst.is_symlink():
        return {"state": "foreign", "target": str(dst), "note": "링크로 된 스킬 폴더 — 관리하지 않는다 (일반 폴더로 바꾸거나 그대로 사용)"}
    if not (dst / "SKILL.md").exists():
        return {"state": "missing", "target": str(dst)}
    m = dst / MANIFEST
    if not m.exists():
        return {"state": "foreign", "target": str(dst),
                "note": "같은 이름의 스킬이 있지만 이 툴킷이 설치한 것이 아님 — 덮어쓰지 않는다 (직접 옮기거나 지운 뒤 다시)"}
    try:
        info = json.loads(m.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "foreign", "target": str(dst), "note": "소유 표시 파일을 읽을 수 없음"}
    edited = skill_digest(dst) != info.get("digest")
    state = "current" if info.get("digest") == want and not edited else "outdated"
    return {"state": state, "target": str(dst), "installed_version": info.get("version"), "bundled_version": __version__,
            **({"note": "설치 뒤 사본이 직접 수정됨 — 다시 설치하면 백업 후 교체"} if edited else {})}


def install_skill(backup_dir: Path, dst: Path | None = None) -> dict:
    dst = dst or skill_destination()
    st = skill_status(dst)
    if st["state"] == "foreign":
        return {"ok": False, "error": st["note"], "target": str(dst)}
    if st["state"] == "current":
        return {"ok": True, "skipped": "이미 최신", "target": str(dst)}
    backup = None
    if dst.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{SKILL_NAME}-{st.get('installed_version')}-{os.getpid()}"
        shutil.copytree(dst, backup, dirs_exist_ok=True)  # 스킬 폴더 밖에 둔다 (스킬로 읽히지 않게)
        shutil.rmtree(dst)
    src = bundled_skill()
    shutil.copytree(src, dst)
    (dst / MANIFEST).write_text(json.dumps({"tool": "oss-analyzer", "version": __version__, "digest": skill_digest(src)},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
    after = skill_status(dst)
    return {"ok": after["state"] == "current", "target": str(dst), "backup": str(backup) if backup else None,
            "state": after["state"], "note": "Claude Code 를 다시 시작하거나 새 세션에서 스킬이 보인다"}


def remove_skill(dst: Path | None = None) -> dict:
    dst = dst or skill_destination()
    st = skill_status(dst)
    if st["state"] == "missing":
        return {"ok": True, "skipped": "설치돼 있지 않음"}
    if st["state"] == "foreign":
        return {"ok": False, "error": "이 툴킷이 설치한 스킬이 아님 — 지우지 않는다", "target": str(dst)}
    shutil.rmtree(dst)
    return {"ok": True, "removed": str(dst)}


# ---------------------------------------------------------------- MCP 등록 (Claude Code)

def mcp_command() -> list[str]:
    """이 설치의 MCP 서버를 띄우는 명령 — 실행 중인 Python 기준이라 사람마다 경로가 달라도 맞다."""
    return [sys.executable, "-m", "oss_analyzer.main"]


def _norm(s: str) -> str:
    return s.replace("\\", "/").lower()


def _claude(args: list[str], cwd: Path | None, timeout: int = 60) -> subprocess.CompletedProcess | None:
    claude = shutil.which("claude")
    if not claude:
        return None
    return subprocess.run([claude, *args], capture_output=True, timeout=timeout, cwd=cwd)


def mcp_status(scope: str = "user", project_dir: Path | None = None) -> dict:
    _check_scope(scope)
    cwd = Path(project_dir or Path.cwd()) if scope == "project" else None
    try:
        proc = _claude(["mcp", "get", MCP_NAME], cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"state": "unknown", "note": f"claude mcp get 실패: {type(e).__name__}"}
    if proc is None:
        return {"state": "unknown", "note": "claude CLI 가 PATH 에 없음 — Claude Code 등록 여부를 확인할 수 없음"}
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    if proc.returncode != 0:
        return {"state": "missing"}
    if _norm(sys.executable) in _norm(text):
        return {"state": "current"}
    return {"state": "foreign", "note": "oss-analyzer 등록이 다른 설치(다른 Python 또는 플러그인)를 가리킴 — 자동으로 바꾸지 않는다. "
                                        "확인 후 'claude mcp remove oss-analyzer' 하고 다시 등록"}


def register_mcp(scope: str = "user", project_dir: Path | None = None) -> dict:
    st = mcp_status(scope, project_dir)
    if st["state"] == "current":
        return {"ok": True, "skipped": "이미 이 설치로 등록됨"}
    if st["state"] != "missing":
        return {"ok": False, "error": st.get("note", st["state"])}
    cwd = Path(project_dir or Path.cwd()) if scope == "project" else None
    proc = _claude(["mcp", "add", "-s", scope, MCP_NAME, "--", *mcp_command()], cwd, timeout=120)
    return {"ok": proc.returncode == 0, "scope": scope, "exit_code": proc.returncode,
            "output": (proc.stdout + proc.stderr).decode("utf-8", "replace")[-800:],
            "note": "Claude Code 를 다시 시작해야 도구가 보인다"
                    + (" (project 범위는 그 프로젝트의 .mcp.json — 팀과 공유하려면 커밋, 개인용이면 커밋하지 않음)" if scope == "project" else "")}


def unregister_mcp(scope: str = "user", project_dir: Path | None = None) -> dict:
    st = mcp_status(scope, project_dir)
    if st["state"] == "missing":
        return {"ok": True, "skipped": "등록돼 있지 않음"}
    if st["state"] != "current":
        return {"ok": False, "error": st.get("note", "이 설치의 등록이 아님 — 지우지 않는다")}
    cwd = Path(project_dir or Path.cwd()) if scope == "project" else None
    proc = _claude(["mcp", "remove", "-s", scope, MCP_NAME], cwd, timeout=120)
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode}


# ---------------------------------------------------------------- CLI

def cli_status() -> dict:
    """어느 폴더에서나 `oss-analyzer` 명령으로 부를 수 있는지 (스킬·다른 세션이 경로를 몰라도 되게)."""
    found = shutil.which("oss-analyzer")
    here = Path(sys.executable).parent
    if not found:
        return {"state": "missing", "this_install": str(here),
                "hint": "PATH 에 없음 — pipx install <툴킷 저장소 경로 또는 git URL> (또는 uv tool install) 로 설치하거나, "
                        "this_install 폴더를 PATH 에 추가"}
    same = Path(found).resolve().parent == here.resolve()
    # pipx·uv tool 은 PATH 쪽에 실행 파일을 따로 둔다 — 폴더가 달라도 같은 설치일 수 있어 경고만
    return {"state": "current" if same else "found", "path": found,
            **({} if same else {"note": "PATH 의 oss-analyzer 가 이 설치와 다른 폴더 — pipx/uv 설치면 정상, 아니면 버전 확인"})}


def status(scope: str = "user", project_dir: Path | None = None) -> dict:
    return {"version": __version__, "scope": scope, "cli": cli_status(),
            "skill": skill_status(skill_destination(scope, project_dir)), "mcp": mcp_status(scope, project_dir)}
