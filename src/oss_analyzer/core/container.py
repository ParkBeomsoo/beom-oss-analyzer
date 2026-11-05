"""컨테이너 실행기 — 네이티브 docker(Docker Desktop 등) 또는 WSL 배포판 안의 Docker Engine.

Docker Desktop 은 일정 규모 이상 회사의 업무 사용에 유료 구독이 필요하다. WSL 배포판에 Docker Engine
(Moby, Apache-2.0)을 설치해 쓰면 그 조건이 없다 → 기본 배포판 이름 'oss-sandbox' 를 자동으로 찾는다
(`oss-analyzer setup --sandbox` 가 만든다).

OSS_ANALYZER_CONTAINER = auto(기본) | docker | wsl | none,  OSS_ANALYZER_WSL_DISTRO = 배포판 이름.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

DEFAULT_DISTRO = "oss-sandbox"
_cache: tuple[float, Engine | None] | None = None
CACHE_SECONDS = 60


@dataclass(frozen=True)
class Engine:
    kind: str  # docker | wsl
    prefix: tuple[str, ...]
    distro: str | None = None
    version: str = ""

    def argv(self, *args: str) -> list[str]:
        return [*self.prefix, *args]

    def path(self, p: Path | str) -> str:
        """호스트 경로 → 컨테이너 엔진이 보는 경로 (WSL 은 /mnt/<드라이브>/...)."""
        if self.kind != "wsl":
            return str(p)
        w = PureWindowsPath(str(p))
        if not w.drive:
            return str(p).replace("\\", "/")
        return f"/mnt/{w.drive[0].lower()}/" + "/".join(w.parts[1:])

    @property
    def label(self) -> str:
        return f"wsl:{self.distro} docker {self.version}" if self.kind == "wsl" else f"docker {self.version}"


def _version_ok(proc: subprocess.CompletedProcess) -> str | None:
    # --format 을 주면 데몬에 못 붙어도 종료 코드가 0 이다 (Windows Docker 26 에서 실제 확인) → 출력으로 판정
    out = proc.stdout.decode("utf-8", "replace").replace("\x00", "").strip()
    err = proc.stderr.decode("utf-8", "replace").lower()
    if proc.returncode == 0 and re.match(r"^\d+\.\d+", out) and "error" not in err:
        return out.split()[0]
    return None


def _native() -> Engine | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    try:
        v = _version_ok(subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=20))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Engine("docker", (docker,), version=v) if v else None


def wsl_distros() -> list[str]:
    if os.name != "nt" or not shutil.which("wsl"):
        return []
    try:
        proc = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = proc.stdout.replace(b"\x00", b"").decode("utf-8", "replace")  # wsl.exe 는 UTF-16 으로 출력
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _wsl(distro: str) -> Engine | None:
    if distro not in wsl_distros():
        return None
    prefix = ("wsl", "-d", distro, "-u", "root", "--", "docker")
    for attempt in range(2):
        try:
            v = _version_ok(subprocess.run([*prefix, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=60))
        except (OSError, subprocess.TimeoutExpired):
            v = None
        if v:
            return Engine("wsl", prefix, distro=distro, version=v)
        if attempt == 0:  # 배포판이 막 깨어나 서비스가 아직 안 떴을 수 있음
            subprocess.run(["wsl", "-d", distro, "-u", "root", "--", "systemctl", "start", "docker"], capture_output=True, timeout=60)
            time.sleep(2)
    return None


def detect(refresh: bool = False) -> Engine | None:
    global _cache
    if not refresh and _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    mode = os.environ.get("OSS_ANALYZER_CONTAINER", "auto").lower()
    distro = os.environ.get("OSS_ANALYZER_WSL_DISTRO", DEFAULT_DISTRO)
    eng = None
    if mode in ("auto", "docker"):
        eng = _native()
    if eng is None and mode in ("auto", "wsl"):
        eng = _wsl(distro)
    _cache = (time.monotonic(), eng)
    return eng
