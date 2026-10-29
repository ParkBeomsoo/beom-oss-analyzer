"""git 어댑터 — 기준 파일 목록과 커밋 고정. 모든 근거는 고정 커밋 기준으로 검증한다."""
from __future__ import annotations

import subprocess
from pathlib import Path

from oss_analyzer.common.response import ToolError


def run(args: list[str], cwd: Path | None = None, input_bytes: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            # core.longpaths: Windows 260자 경로 제한 (깊은 테스트 스냅샷 경로가 있는 저장소에서 checkout 실패)
            ["git", "-c", "core.quotepath=off", "-c", "core.longpaths=true", *args],
            cwd=cwd, input=input_bytes, capture_output=True, timeout=600,
        )
    except FileNotFoundError as e:
        raise ToolError("capability_unavailable", "git 이 설치돼 있지 않음") from e
    if proc.returncode != 0:
        raise ToolError("engine_error", f"git {' '.join(args)} 실패: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def version() -> str | None:
    try:
        return run(["--version"]).decode().strip()
    except ToolError:
        return None


def remove_tree(path: Path) -> None:
    """툴킷이 만든 clone 폴더 삭제. Windows 에서 git 팩 파일이 읽기 전용이라 속성을 풀고 지운다."""
    import os
    import shutil
    import stat

    def _force(func, target, _exc):  # noqa: ANN001
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onerror=_force)


def clone(source: str, dest: Path) -> None:
    """임시 폴더에 받은 뒤 성공했을 때만 dest 로 옮긴다 (실패한 반쪽 clone 이 재사용되지 않도록)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    remove_tree(tmp)
    try:
        run(["-c", "advice.detachedHead=false", "clone", "--no-hardlinks", "--quiet", source, str(tmp)])
    except ToolError:
        remove_tree(tmp)
        raise
    tmp.rename(dest)


def checkout_detached(repo: Path, commit: str) -> None:
    run(["checkout", "--quiet", "--detach", commit], cwd=repo)


def resolve_commit(repo: Path, ref: str = "HEAD") -> str:
    return run(["rev-parse", f"{ref}^{{commit}}"], cwd=repo).decode().strip()


def ls_files(repo: Path, commit: str) -> list[str]:
    out = run(["ls-tree", "-r", "-z", "--name-only", commit], cwd=repo)
    return [p for p in out.decode("utf-8").split("\0") if p]


def show(repo: Path, commit: str, path: str) -> bytes:
    return run(["show", f"{commit}:{path}"], cwd=repo)


def file_exists(repo: Path, commit: str, path: str) -> bool:
    try:
        run(["cat-file", "-e", f"{commit}:{path}"], cwd=repo)
        return True
    except ToolError:
        return False


def blob_shas(repo: Path, commit: str) -> dict[str, str]:
    out = run(["ls-tree", "-r", "-z", commit], cwd=repo)
    result = {}
    for entry in out.decode("utf-8").split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        result[path] = meta.split()[2]
    return result
