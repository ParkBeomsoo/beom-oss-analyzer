"""체크포인트 — 라운드마다 대상 폴더의 스냅샷을 남기고, 변경 사항(diff)을 보여 주고, 되돌린다.

사용자의 git 저장소(.git, 인덱스, 브랜치)는 건드리지 않는다. 실행 폴더 안에 따로 둔 숨은 저장소
(<run>/shadow.git)를 GIT_DIR 로, 대상 폴더를 작업 트리로 쓴다. 대상의 .gitignore 는 그대로 존중된다.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from oss_analyzer.common.response import ToolError

EXCLUDE = [".git/", ".oss-analyzer/", "node_modules/", "target/", "__pycache__/", ".venv/", "venv/", "dist/", "build/"]
MAX_DIFF = 400_000
_LIST_CACHE: dict[str, tuple] = {}


def _git(shadow: Path, root: Path, *args: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_DIR=str(shadow), GIT_WORK_TREE=str(root), GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_NAME="oss-analyzer", GIT_AUTHOR_EMAIL="checkpoint@oss-analyzer.local",
               GIT_COMMITTER_NAME="oss-analyzer", GIT_COMMITTER_EMAIL="checkpoint@oss-analyzer.local")
    proc = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", *args], cwd=root, env=env,
                          capture_output=True, text=text, encoding="utf-8" if text else None, errors="replace" if text else None,
                          timeout=300)
    if check and proc.returncode != 0:
        raise ToolError("internal", f"git {' '.join(args[:2])} 실패: {(proc.stderr or '')[-400:]}")
    return proc


class Checkpoints:
    def __init__(self, run_dir: Path, target_root: str | Path):
        self.shadow = Path(run_dir) / "shadow.git"
        self.root = Path(target_root)

    def _init(self) -> None:
        if (self.shadow / "HEAD").exists():
            return
        self.shadow.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-q", str(self.shadow)], check=True, capture_output=True, timeout=60)
        info = self.shadow / "info"
        info.mkdir(exist_ok=True)
        (info / "exclude").write_text("\n".join(EXCLUDE) + "\n", encoding="utf-8")

    def snapshot(self, label: str) -> str | None:
        """지금 대상 폴더 상태를 기록. 바뀐 게 없으면 새로 만들지 않고 마지막 것을 돌려준다."""
        self.root.mkdir(parents=True, exist_ok=True)
        self._init()
        _git(self.shadow, self.root, "add", "-A", ".")
        head = _git(self.shadow, self.root, "rev-parse", "-q", "--verify", "HEAD", check=False).stdout.strip()
        if head and not _git(self.shadow, self.root, "diff", "--cached", "--quiet", check=False).returncode:
            return head
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _git(self.shadow, self.root, "commit", "-q", "--allow-empty", "-m", f"{label}\n\n{stamp}")
        _git(self.shadow, self.root, "gc", "--auto", "--quiet", check=False)  # 라운드가 쌓여도 크기가 불지 않게 (필요할 때만 압축)
        return _git(self.shadow, self.root, "rev-parse", "HEAD").stdout.strip()

    def _stamp(self) -> tuple:
        """체크포인트가 새로 생겼는지 — 브랜치 참조 파일의 수정 시각 (앱이 자주 다시 읽어도 git 을 부르지 않게)."""
        out = []
        for p in (self.shadow / "refs" / "heads", self.shadow / "packed-refs"):
            try:
                if p.is_dir():
                    out += sorted((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in p.iterdir())
                else:
                    out.append((p.name, p.stat().st_mtime_ns))
            except OSError:
                pass
        return tuple(out)

    def list(self) -> list[dict]:
        if not (self.shadow / "HEAD").exists():
            return []
        key = str(self.shadow)
        stamp = self._stamp()
        hit = _LIST_CACHE.get(key)
        if hit and hit[0] == stamp:
            return [dict(r) for r in hit[1]]
        out = _git(self.shadow, self.root, "log", "--format=%H%x1f%s%x1f%cI", check=False).stdout
        rows = []
        for line in out.splitlines():
            h, s, at = (line.split("\x1f") + ["", ""])[:3]
            if h:
                rows.append({"id": h, "label": s, "at": at})
        _LIST_CACHE[key] = (stamp, rows)
        return [dict(r) for r in rows]

    def changes(self, base: str | None = None) -> dict:
        """처음(또는 base) 체크포인트 대비 지금 대상 폴더의 변경 — 파일 목록과 diff."""
        cps = self.list()
        if not cps:
            return {"files": [], "diff": "", "base": None}
        base = base or cps[-1]["id"]
        self._init()
        _git(self.shadow, self.root, "add", "-A", ".")
        stat = _git(self.shadow, self.root, "diff", "--cached", "--numstat", "--no-renames", base, check=False).stdout
        names = _git(self.shadow, self.root, "diff", "--cached", "--name-status", "--no-renames", base, check=False).stdout
        status = {}
        for line in names.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status[parts[-1]] = parts[0][0]
        files = []
        for line in stat.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                add, rem, path = parts
                path = path.split(" => ")[-1].rstrip("}") if " => " in path else path
                files.append({"path": path, "added": None if add == "-" else int(add), "removed": None if rem == "-" else int(rem),
                              "status": status.get(path, "M")})
        diff = _git(self.shadow, self.root, "diff", "--cached", "--no-renames", base, check=False).stdout
        return {"files": files, "diff": diff[:MAX_DIFF], "truncated": len(diff) > MAX_DIFF, "base": base}

    def restore(self, checkpoint: str) -> dict:
        """대상 폴더를 그 체크포인트 상태로 되돌린다. 되돌리기 전 상태도 체크포인트로 남겨 다시 되돌릴 수 있다."""
        known = {c["id"] for c in self.list()}
        if checkpoint not in known:
            raise ToolError("not_found", "없는 체크포인트")
        before = self.snapshot("되돌리기 전")
        added = _git(self.shadow, self.root, "diff", "--name-only", "--no-renames", "--diff-filter=A", checkpoint, before).stdout.splitlines()
        for rel in added:
            p = (self.root / rel)
            if p.is_file() and str(p.resolve()).startswith(str(self.root.resolve())):
                p.unlink()
        _git(self.shadow, self.root, "checkout", "-q", checkpoint, "--", ".")
        after = self.snapshot("되돌림")
        return {"restored": checkpoint, "removed_files": added, "undo": before, "now": after}
