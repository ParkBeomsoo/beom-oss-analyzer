"""LSP 어댑터 — multilspy(MIT)로 언어 서버를 띄우고 참조·호출 계층을 조회한다.

- 언어 서버는 (저장소, 언어)별로 필요할 때 띄우고, 유휴 시간이 지나면 종료한다 (상주 프로세스 없음).
- multilspy 가 받아오는 rust-analyzer 는 2023년 버전이라, 시스템에 설치된 rust-analyzer 를 우선 사용한다.
- 호출 계층은 multilspy 고수준 API 가 없어서 raw LSP 요청(callHierarchy/*)으로 보낸다.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from oss_analyzer.common.response import ToolError
from oss_analyzer.core.levels import LSP_LANGUAGES

# multilspy 의 code_language 값으로 변환 (c 는 clangd = cpp 로 처리)
MULTILSPY_LANGUAGE = {"c": "cpp", "javascript": "javascript", "typescript": "typescript"}

# 언어별로 필요한 외부 실행 파일 (없으면 multilspy 가 자체 다운로드하는 언어는 None)
REQUIRED_BINARY = {
    "rust": "rust-analyzer",
    "typescript": "node",
    "javascript": "node",
    "go": "gopls",
    "java": "java",
    "csharp": "dotnet",
    "ruby": "ruby",
    "kotlin": "java",
    "dart": "dart",
    "cpp": "clangd",
    "c": "clangd",
    "python": None,  # jedi-language-server 는 multilspy 의존성으로 함께 설치됨
}


def _rust_toolchain() -> tuple[str | None, str | None]:
    """(rust-analyzer 실제 바이너리 경로, 그 툴체인 이름).

    분석 대상이 rust-toolchain.toml 로 툴체인을 고정하면, 저장소 안에서 실행한 rustup shim 은 그 툴체인으로
    바뀌는데 거기엔 rust-analyzer 가 없을 수 있다 → 서버가 즉시 죽는다 (Meilisearch 에서 실제 발생).
    또 rustup 이 그 툴체인을 자동으로 내려받으려 할 수 있다. 그래서 홈 디렉터리 기준 툴체인을 고정해 쓴다.
    """
    rustup = shutil.which("rustup")
    if rustup:
        proc = subprocess.run([rustup, "which", "rust-analyzer"], capture_output=True, timeout=60, cwd=str(Path.home()))
        if proc.returncode == 0:
            path = proc.stdout.decode().strip()
            return path, Path(path).parent.parent.name
    path = shutil.which("rust-analyzer")
    return path, None


def _system_rust_analyzer() -> str | None:
    path, _ = _rust_toolchain()
    if not path:
        return None
    proc = subprocess.run([path, "--version"], capture_output=True, timeout=60, cwd=str(Path.home()))
    return path if proc.returncode == 0 else None  # rustup shim 만 있고 컴포넌트가 없으면 실패


def language_available(language: str) -> tuple[bool, str]:
    if language not in LSP_LANGUAGES:
        return False, "허용된 LSP 언어 목록에 없음"
    try:
        import multilspy  # noqa: F401
    except ImportError:
        return False, "multilspy 미설치"
    if language == "rust":
        return (True, "system rust-analyzer") if _system_rust_analyzer() else (False, "rustup component add rust-analyzer 필요")
    binary = REQUIRED_BINARY.get(language)
    if binary and not shutil.which(binary):
        return False, f"{binary} 가 PATH 에 없음"
    return True, "ok"


def _patch_rust_analyzer() -> None:
    """시스템 rust-analyzer 사용 + 분석 대상 코드를 실행하는 기능(빌드 스크립트·proc-macro·cargo check) 끄기.

    multilspy 기본값은 build.rs 와 proc-macro 를 컴파일·실행한다 → 신뢰할 수 없는 OSS 코드가 내 PC 에서 돈다.
    끄면 proc-macro 가 만드는 항목은 해석되지 않으므로, 그 부분은 미확인으로 남겨야 한다 (SKILL.md 참고).
    """
    from multilspy.language_servers.rust_analyzer.rust_analyzer import RustAnalyzer

    if getattr(RustAnalyzer, "_oss_patched", False):
        return
    system = _system_rust_analyzer()
    if system:
        RustAnalyzer.setup_runtime_dependencies = lambda self, logger, config: system  # type: ignore[method-assign]
    original = RustAnalyzer._get_initialize_params

    def safe_params(self, repository_absolute_path):  # type: ignore[no-untyped-def]
        params = original(self, repository_absolute_path)
        opts = params.get("initializationOptions", {})
        opts.setdefault("cargo", {}).setdefault("buildScripts", {})["enable"] = False
        opts.setdefault("procMacro", {})["enable"] = False
        opts["checkOnSave"] = False
        return params

    RustAnalyzer._get_initialize_params = safe_params  # type: ignore[method-assign]
    RustAnalyzer._oss_patched = True  # type: ignore[attr-defined]


def _global_ts_server() -> list[str] | None:
    """전역 npm 의 typescript-language-server 를 node 로 직접 실행 (Windows 에서 확장자 없는 셸 스크립트 실행 실패 회피)."""
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        return None
    try:
        root = subprocess.run([npm, "root", "-g"], capture_output=True, timeout=60, shell=False).stdout.decode().strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    cli = Path(root) / "typescript-language-server" / "lib" / "cli.mjs"
    return [node, str(cli), "--stdio"] if cli.exists() else None


def _global_tsserver() -> str | None:
    """분석 clone 에는 node_modules 가 없으므로(npm install 은 대상 코드 실행 위험이라 안 함) 툴킷 전용 tsserver 를 쓴다.

    우선순위: <home>/node/node_modules/typescript (툴킷 전용 TS 5) → 전역 typescript.
    TypeScript 7(Go 네이티브)은 tsserver.js 가 없어 typescript-language-server 와 호환되지 않는다.
    """
    from oss_analyzer.config import load_settings

    candidates = [load_settings().home / "node" / "node_modules" / "typescript" / "lib" / "tsserver.js"]
    npm = shutil.which("npm")
    if npm:
        root = subprocess.run([npm, "root", "-g"], capture_output=True, timeout=60).stdout.decode().strip()
        candidates.append(Path(root) / "typescript" / "lib" / "tsserver.js")
    return next((str(p) for p in candidates if p.exists()), None)


def _patch_typescript() -> None:
    from multilspy.language_servers.typescript_language_server.typescript_language_server import TypeScriptLanguageServer

    if getattr(TypeScriptLanguageServer, "_oss_patched", False):
        return
    cmd = _global_ts_server()
    if cmd:
        TypeScriptLanguageServer.setup_runtime_dependencies = lambda self, logger, config: cmd  # type: ignore[method-assign]
    tsserver = _global_tsserver()
    if tsserver:
        original = TypeScriptLanguageServer._get_initialize_params

        def with_tsserver(self, repository_absolute_path):  # type: ignore[no-untyped-def]
            params = original(self, repository_absolute_path)
            params.setdefault("initializationOptions", {})["tsserver"] = {"path": tsserver}
            return params

        TypeScriptLanguageServer._get_initialize_params = with_tsserver  # type: ignore[method-assign]
    TypeScriptLanguageServer._oss_patched = True  # type: ignore[attr-defined]


def _preflight(cmd: list[str], repo: Path, name: str) -> None:
    """분석 대상 저장소 위치에서 서버 실행 파일이 실제로 도는지 먼저 확인 (툴체인 고정 등으로 즉시 죽는 경우를 잡는다)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120, cwd=str(repo))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ToolError("capability_unavailable", f"{name} 실행 확인 실패: {e}") from e
    if proc.returncode != 0:
        raise ToolError("capability_unavailable",
                        f"{name} 가 이 저장소에서 실행되지 않음: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}",
                        recovery="rustup component add rust-analyzer (해당 툴체인) 또는 oss_doctor 확인")


class _Server:
    def __init__(self, repo: Path, language: str, timeout: int) -> None:
        from multilspy import SyncLanguageServer
        from multilspy.multilspy_config import MultilspyConfig
        from multilspy.multilspy_logger import MultilspyLogger

        if language == "rust":
            _patch_rust_analyzer()
            path, toolchain = _rust_toolchain()
            if toolchain:
                # 언어 서버가 부르는 cargo 등도 같은 툴체인을 쓰게 (대상 저장소의 툴체인 고정·자동 설치 무시)
                os.environ["RUSTUP_TOOLCHAIN"] = toolchain
            _preflight([path or "rust-analyzer", "--version"], repo, "rust-analyzer")
        if language in ("typescript", "javascript"):
            _patch_typescript()
        config = MultilspyConfig.from_dict({"code_language": MULTILSPY_LANGUAGE.get(language, language)})
        self.timeout = timeout
        self.lsp = SyncLanguageServer.create(config, MultilspyLogger(), str(repo), timeout=timeout)
        self._stack = ExitStack()
        self._start_with_watchdog(language, timeout)
        # tsserver 는 '첫 요청'을 받아야 프로젝트(tsconfig) 로딩을 시작하고, 그 요청에는 같은 파일 결과만 준다.
        # 그래서 첫 질의 전에 같은 질의를 한 번 버려서 로딩을 시작시키고 잠시 기다린다.
        self._needs_priming = language in ("typescript", "javascript")
        self.last_used = time.monotonic()
        self.lock = threading.Lock()

    def _start_with_watchdog(self, language: str, timeout: int) -> None:
        """서버 기동을 별도 스레드에서 하고, 서버 프로세스가 죽거나 시간을 넘기면 즉시 실패시킨다.

        multilspy 는 기동 중 서버가 죽어도 initialize 응답을 계속 기다려 영원히 멈췄다 (잠금도 같이 붙잡힘).
        """
        error: list[BaseException] = []

        def start() -> None:
            try:
                self._stack.enter_context(self.lsp.start_server())
            except BaseException as e:  # noqa: BLE001
                error.append(e)

        thread = threading.Thread(target=start, daemon=True, name=f"lsp-start-{language}")
        thread.start()
        deadline = time.monotonic() + timeout
        while thread.is_alive():
            thread.join(1.0)
            server = getattr(self.lsp.language_server, "server", None)
            proc = getattr(server, "process", None)
            if proc is not None and proc.returncode is not None:
                raise ToolError("engine_error", f"{language} 언어 서버가 기동 중 종료됨 (exit {proc.returncode})",
                                recovery="oss_doctor 로 언어 서버 설치 상태 확인")
            if time.monotonic() > deadline:
                raise ToolError("engine_error", f"{language} 언어 서버가 {timeout}초 안에 준비되지 않음",
                                recovery="OSS_ANALYZER_LSP_TIMEOUT_SECONDS 를 늘리거나 저장소 크기 확인")
        if error:
            raise error[0]

    def _prime(self, rel_path: str, line: int, column: int) -> None:
        """결과 파일 수가 두 번 연속 그대로일 때까지 같은 질의를 반복 (실측: 기동 후 두 번째 요청·4~6초 뒤부터 파일 간 결과)."""
        if not self._needs_priming:
            return
        self._needs_priming = False
        deadline = time.monotonic() + float(os.environ.get("OSS_ANALYZER_TS_WARMUP_SECONDS", "20"))
        prev, stable = -1, 0
        while time.monotonic() < deadline:
            try:
                size = len({r.get("relativePath") for r in self.lsp.request_references(rel_path, line - 1, column - 1)})
            except Exception:  # noqa: BLE001 — 예열 실패는 본 질의에서 다시 드러난다
                return
            stable = stable + 1 if size == prev else 0
            if stable >= 2 and size > 1 or stable >= 4:
                return
            prev = size
            time.sleep(2)

    def close(self) -> None:
        self._stack.close()

    def _raw(self, coro_factory) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro_factory(), self.lsp.loop)
        return future.result(timeout=self.timeout)

    def references(self, rel_path: str, line: int, column: int) -> list[dict]:
        with self.lock:
            self._prime(rel_path, line, column)
            self.last_used = time.monotonic()
            return [dict(loc) for loc in self.lsp.request_references(rel_path, line - 1, column - 1)]

    def definition(self, rel_path: str, line: int, column: int) -> list[dict]:
        with self.lock:
            self._prime(rel_path, line, column)
            self.last_used = time.monotonic()
            return [dict(loc) for loc in self.lsp.request_definition(rel_path, line - 1, column - 1)]

    def call_hierarchy(self, rel_path: str, line: int, column: int, direction: str) -> list[dict]:
        server = self.lsp.language_server.server
        uri = (Path(self.lsp.language_server.repository_root_path) / rel_path).resolve().as_uri()
        with self.lock:
            self._prime(rel_path, line, column)
        with self.lock, self.lsp.open_file(rel_path):
            self.last_used = time.monotonic()
            items = self._raw(lambda: server.send.prepare_call_hierarchy({
                "textDocument": {"uri": uri}, "position": {"line": line - 1, "character": column - 1},
            })) or []
            results = []
            for item in items:
                if direction == "incoming":
                    calls = self._raw(lambda item=item: server.send.incoming_calls({"item": item})) or []
                    results.extend({"item": item, "peer": c["from"], "ranges": c.get("fromRanges", [])} for c in calls)
                else:
                    calls = self._raw(lambda item=item: server.send.outgoing_calls({"item": item})) or []
                    results.extend({"item": item, "peer": c["to"], "ranges": c.get("fromRanges", [])} for c in calls)
            return results


class ProcessLock:
    """프로세스 간 잠금 — 같은 (저장소, 언어) 언어 서버를 여러 CLI 프로세스가 동시에 띄우지 않게 한다.

    병렬 에이전트 4개가 jdtls 를 각자 띄우자 배치 하나가 10분을 넘었다. 줄을 세우면 각자 금방 끝난다.
    mkdir 은 원자적이라 OS 공통으로 쓸 수 있다. 오래된 잠금(프로세스 비정상 종료)은 stale_seconds 후 회수.
    """

    def __init__(self, path: Path, stale_seconds: int = 3600) -> None:
        self.path = path
        self.stale_seconds = stale_seconds

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        waited = 0.0
        while True:
            try:
                self.path.mkdir()
                (self.path / "pid").write_text(str(os.getpid()), encoding="utf-8")
                return self
            except FileExistsError:
                if self._stale():
                    self._release()
                    continue
                if waited == 0 or int(waited) % 60 == 0:
                    import sys
                    print(f"[oss-analyzer] 언어 서버 대기 중 ({self.path.name}, {int(waited)}s)", file=sys.stderr, flush=True)
                time.sleep(2)
                waited += 2

    def _stale(self) -> bool:
        """잠금을 만든 프로세스가 죽었거나(PID 확인), 너무 오래됐으면 회수 대상."""
        try:
            pid = int((self.path / "pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            try:  # pid 파일을 쓰기 전 찰나이거나 예전 형식 — 오래됐을 때만 회수
                return time.time() - self.path.stat().st_mtime > 30
            except OSError:
                return False
        if not _pid_alive(pid):
            return True
        try:
            return time.time() - self.path.stat().st_mtime > self.stale_seconds
        except OSError:
            return False

    def _release(self) -> None:
        try:
            (self.path / "pid").unlink(missing_ok=True)
            self.path.rmdir()
        except OSError:
            pass

    def __exit__(self, *exc) -> None:
        self._release()


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) 은 Windows 에서 프로세스를 종료시키므로 쓰면 안 된다 → WinAPI 로 상태만 조회
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() == 5  # 접근 거부 = 살아 있음
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class LspPool:
    """(저장소, 언어) → 언어 서버. 유휴 서버는 reap() 에서 종료."""

    def __init__(self, idle_seconds: int, timeout_seconds: int = 900) -> None:
        self.idle_seconds = idle_seconds
        self.timeout_seconds = timeout_seconds
        self._servers: dict[tuple[str, str], _Server] = {}
        self._lock = threading.Lock()

    def get(self, repo: Path, language: str) -> _Server:
        ok, reason = language_available(language)
        if not ok:
            raise ToolError("capability_unavailable", f"{language} LSP 사용 불가: {reason}", recovery="oss_doctor 로 설치 안내 확인")
        self.reap()
        key = (str(repo), language)
        with self._lock:
            if key not in self._servers:
                try:
                    self._servers[key] = _Server(repo, language, self.timeout_seconds)
                except Exception as e:  # noqa: BLE001
                    raise ToolError("engine_error", f"{language} 언어 서버 시작 실패: {e}") from e
            return self._servers[key]

    def reap(self) -> None:
        now = time.monotonic()
        with self._lock:
            for key, server in list(self._servers.items()):
                if now - server.last_used > self.idle_seconds:
                    server.close()
                    del self._servers[key]

    def close_repo(self, repo: Path) -> None:
        with self._lock:
            for key in [k for k in self._servers if k[0] == str(repo)]:
                self._servers.pop(key).close()
