"""환경 점검 — 엔진 설치 여부·버전과 설치 안내. 분석 대상 언어를 주면 그 언어에 필요한 것만 본다."""
from __future__ import annotations

import shutil
import subprocess

from oss_analyzer.adapters import git
from oss_analyzer.adapters.astgrep import AstGrep
from oss_analyzer.adapters.cbm import CodebaseMemory
from oss_analyzer.adapters.lsp import language_available
from oss_analyzer.config import Settings
from oss_analyzer.core.levels import LSP_LANGUAGES

INSTALL_HINTS = {
    "git": "https://git-scm.com/downloads",
    "codebase-memory-mcp": "https://github.com/DeusData/codebase-memory-mcp/releases 에서 OS 별 zip → <home>/bin 에 배치 (checksums.txt 로 검증)",
    "ast-grep": "npm i -g @ast-grep/cli  또는  cargo install ast-grep --locked",
    "lsp:rust": "rustup component add rust-analyzer",
    "lsp:typescript": "Node.js 설치 (typescript-language-server 는 multilspy 가 자동 설치)",
    "lsp:javascript": "Node.js 설치",
    "lsp:python": "multilspy 의존성(jedi-language-server)으로 설치됨",
    "lsp:go": "go install golang.org/x/tools/gopls@latest",
    "lsp:java": "JDK 17+ 설치 (jdtls 는 multilspy 가 자동 설치, EPL-2.0 — 외부 프로세스로만 사용)",
    "lsp:csharp": ".NET SDK 설치",
    "lsp:ruby": "Ruby 설치 (gem install solargraph)",
    "lsp:kotlin": "JDK 설치",
    "lsp:dart": "Dart SDK 설치",
    "lsp:cpp": "clangd 설치 (LLVM)",
    "lsp:c": "clangd 설치 (LLVM)",
    "docker": "격리 실행(3단계 L3)에 필요 — Docker Desktop 또는 WSL2",
    "joern": "https://github.com/joernio/joern/releases 의 joern-cli-<os>.zip (sha512 검증) → <home>/engines/joern-cli, JDK 21+",
}


def _version(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or proc.stderr).decode("utf-8", "replace").strip().splitlines()
    return text[0] if text and proc.returncode == 0 else None


def run(settings: Settings, languages: list[str] | None = None) -> dict:
    from oss_analyzer.adapters.joern import Joern

    checks = {
        "git": git.version(),
        "codebase-memory-mcp": CodebaseMemory(settings).version(),
        "ast-grep": AstGrep(settings).version(),
        "joern": Joern(settings.home).version(),
        "docker": _version(["docker", "--version"]),
    }
    required = ["git", "codebase-memory-mcp", "ast-grep"]
    langs = sorted(set(languages) if languages else LSP_LANGUAGES)
    lsp = {}
    for lang in langs:
        ok, reason = language_available(lang)
        lsp[lang] = {"ok": ok, "reason": reason}
    missing = [name for name in required if not checks[name]]
    hints = {name: INSTALL_HINTS[name] for name in missing}
    for lang, info in lsp.items():
        if not info["ok"] and f"lsp:{lang}" in INSTALL_HINTS:
            hints[f"lsp:{lang}"] = INSTALL_HINTS[f"lsp:{lang}"]
    return {
        "home": str(settings.home),
        "engines": checks,
        "lsp": lsp,
        "required_missing": missing,
        "ready": not missing,
        "install_hints": hints,
        "not_yet": {"coverage": "3단계 (L3 실행 검증)", "scancode": "3단계 (라이선스)"},
        "notes": {"joern": "L2 데이터 흐름. JDK 21+ 필요(OSS_ANALYZER_JAVA_HOME 또는 ~/.jdks 자동 탐색). "
                           "저장소마다 처음 CPG 생성이 오래 걸림(1만 줄 Java 약 15분) — dataflow --build-only 로 미리 생성"},
    }
