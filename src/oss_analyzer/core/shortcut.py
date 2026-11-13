"""데스크톱 앱 바로가기 — 시작 메뉴(검색에 나옴)·바탕화면에서 앱을 열 수 있게.

Windows 는 기본 제공 WScript.Shell 로 .lnk 를 만든다 (추가 설치 없음). Linux 는 ~/.local/share/applications 의 .desktop.
아이콘(.ico/.png)은 앱이 그려서 툴킷 홈에 둔다 (app/main.py: write_icon_files).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings

NAME = "beom-oss-analyzer"
LOCATIONS = ("start", "desktop")


def app_exe() -> Path | None:
    """앱 실행 파일 — 같은 환경(venv·pipx)의 gui-script."""
    here = Path(sys.executable).parent
    for cand in (here / f"{NAME}.exe", here / "Scripts" / f"{NAME}.exe", here / NAME):
        if cand.is_file():
            return cand
    found = shutil.which(NAME)
    return Path(found) if found else None


def _windows_dir(which: str) -> Path:
    if which == "start":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                         capture_output=True, text=True, timeout=30)
    return Path(out.stdout.strip() or Path.home() / "Desktop")


def paths() -> dict[str, Path]:
    if sys.platform == "win32":
        return {w: _windows_dir(w) / f"{NAME}.lnk" for w in LOCATIONS}
    if sys.platform.startswith("linux"):
        return {"start": Path.home() / ".local" / "share" / "applications" / f"{NAME}.desktop",
                "desktop": Path.home() / "Desktop" / f"{NAME}.desktop"}
    return {}


def status() -> dict:
    return {"supported": bool(paths()), "exe": str(app_exe() or ""),
            "installed": {w: p.is_file() for w, p in paths().items()}}


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def create(settings: Settings, where: tuple[str, ...] = LOCATIONS, icon: Path | None = None) -> dict:
    exe = app_exe()
    if exe is None:
        raise ToolError("not_found", f"앱 실행 파일({NAME})을 찾지 못함", recovery='pip install "beom-oss-analyzer[app]" 로 앱 구성요소를 설치')
    made = {}
    for w in where:
        dst = paths().get(w)
        if dst is None:
            raise ToolError("capability_unavailable", "이 운영체제에서는 바로가기를 만들 수 없음")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            # 경로는 환경 변수로 넘긴다 — 스크립트 글자에 넣으면 경로 속 따옴표(’ 등)가 명령으로 읽힐 수 있다
            env = dict(os.environ, OSS_LNK=str(dst), OSS_EXE=str(exe), OSS_HOME=str(settings.home),
                       OSS_ICON=(str(icon) + ",0") if icon and icon.is_file() else "")
            script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:OSS_LNK); "
                      "$s.TargetPath = $env:OSS_EXE; $s.WorkingDirectory = $env:OSS_HOME; "
                      "$s.Description = 'beom-oss-analyzer'; if ($env:OSS_ICON) { $s.IconLocation = $env:OSS_ICON }; $s.Save()")
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60, env=env)
            if proc.returncode != 0 or not dst.is_file():
                raise ToolError("internal", f"바로가기를 만들지 못함: {(proc.stderr or proc.stdout).strip()[-300:]}")
        else:
            dst.write_text("[Desktop Entry]\nType=Application\nName=beom-oss-analyzer\nComment=오픈소스 분석·요청사항 만들기·검증\n"
                           f"Exec={exe}\n" + (f"Icon={icon}\n" if icon and icon.is_file() else "") + "Terminal=false\nCategories=Development;\n",
                           encoding="utf-8")
            dst.chmod(0o755)
        made[w] = str(dst)
    return {"created": made, "exe": str(exe)}


def remove(where: tuple[str, ...] = LOCATIONS) -> dict:
    gone = []
    for w in where:
        p = paths().get(w)
        if p and p.is_file():
            p.unlink()
            gone.append(str(p))
    return {"removed": gone}
