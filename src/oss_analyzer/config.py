"""환경 설정 — 모든 경로·한도는 환경변수로 덮어쓸 수 있다."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _home() -> Path:
    return Path(os.environ.get("OSS_ANALYZER_HOME", Path.home() / ".oss-analyzer"))


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


@dataclass(frozen=True)
class Settings:
    home: Path
    cbm_bin: str | None
    cbm_cache: Path
    astgrep_bin: str | None
    lease_seconds: int
    unit_max_lines: int
    context_max_chars: int
    lsp_idle_seconds: int
    lsp_timeout_seconds: int = 900
    unit_max_chars: int = 45000

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"


def load_settings() -> Settings:
    home = _home()
    bundled_cbm = home / "bin" / ("codebase-memory-mcp.exe" if os.name == "nt" else "codebase-memory-mcp")
    cbm = os.environ.get("OSS_ANALYZER_CBM") or (str(bundled_cbm) if bundled_cbm.exists() else _which("codebase-memory-mcp"))
    return Settings(
        home=home,
        cbm_bin=cbm,
        cbm_cache=Path(os.environ.get("OSS_ANALYZER_CBM_CACHE", home / "cbm-cache")),
        astgrep_bin=os.environ.get("OSS_ANALYZER_ASTGREP") or _which("ast-grep", "sg"),
        lease_seconds=int(os.environ.get("OSS_ANALYZER_LEASE_SECONDS", "3600")),
        unit_max_lines=int(os.environ.get("OSS_ANALYZER_UNIT_MAX_LINES", "1500")),
        # 한 페이지가 에이전트 도구 출력 한도(~30KB) 안에 들어가도록
        context_max_chars=int(os.environ.get("OSS_ANALYZER_CONTEXT_MAX_CHARS", "24000")),
        unit_max_chars=int(os.environ.get("OSS_ANALYZER_UNIT_MAX_CHARS", "45000")),
        lsp_idle_seconds=int(os.environ.get("OSS_ANALYZER_LSP_IDLE_SECONDS", "600")),
        # 큰 저장소는 언어 서버 첫 인덱싱이 오래 걸림
        lsp_timeout_seconds=int(os.environ.get("OSS_ANALYZER_LSP_TIMEOUT_SECONDS", "900")),
    )
