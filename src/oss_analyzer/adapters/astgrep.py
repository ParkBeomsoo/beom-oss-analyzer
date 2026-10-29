"""ast-grep 어댑터 — 규칙팩(rulepacks/*.yml)으로 진입점을 전수 수집한다."""
from __future__ import annotations

import json
import subprocess
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from oss_analyzer.common.response import ToolError
from oss_analyzer.config import Settings

STRING_LITERAL_PREFIXES = ('"', "'", "`", 'r"', "r#\"")


def load_rulepacks(extra_dirs: list[Path] | None = None) -> list[dict]:
    """기본 규칙팩 + 추가 규칙팩 (<home>/rulepacks, 프로젝트 작업 공간/rulepacks, OSS_ANALYZER_RULEPACKS)."""
    packs = []
    for entry in resources.files("oss_analyzer.rulepacks").iterdir():
        if entry.name.endswith(".yml"):
            packs.append(yaml.safe_load(entry.read_text(encoding="utf-8")))
    for d in extra_dirs or []:
        if d.is_dir():
            for f in sorted(d.glob("*.yml")):
                packs.append(yaml.safe_load(f.read_text(encoding="utf-8")))
    return packs


def _dependency_names(repo: Path, path: str) -> set[str] | None:
    """매니페스트에서 런타임 의존성 이름만 (dev·build·test 의존성 제외). 지원 안 하는 형식이면 None."""
    import json
    import tomllib

    name = Path(path).name.lower()
    try:
        text = (repo / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    try:
        if name == "cargo.toml":
            data = tomllib.loads(text)
            deps: set[str] = set()
            for section in ("dependencies",):
                deps |= set(data.get(section, {}))
            deps |= set(data.get("workspace", {}).get("dependencies", {}))
            for target in data.get("target", {}).values():
                deps |= set(target.get("dependencies", {}))
            return deps
        if name == "package.json":
            data = json.loads(text)
            return set(data.get("dependencies", {})) | set(data.get("peerDependencies", {}))
    except (ValueError, tomllib.TOMLDecodeError):
        return None
    return None


def detect(spec: dict, repo: Path, files: list[str]) -> bool:
    if "extensions" in spec:
        return any(Path(f).suffix in spec["extensions"] for f in files)
    wanted = {n.lower() for n in spec.get("manifests", [])}
    tokens = spec.get("contains", [])
    for path in files:
        if Path(path).name.lower() not in wanted:
            continue
        deps = _dependency_names(repo, path)
        if deps is not None:
            # 의존성 이름 정확히 일치 (따옴표가 붙은 토큰 "express" 도 허용)
            if any(t.strip('"') in deps for t in tokens):
                return True
            continue
        try:
            text = (repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(t in text for t in tokens):
            return True
    return False


class AstGrep:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return bool(self.settings.astgrep_bin)

    def version(self) -> str | None:
        if not self.available:
            return None
        proc = subprocess.run([self.settings.astgrep_bin, "--version"], capture_output=True, timeout=60)
        return proc.stdout.decode().strip() or None

    def scan(self, repo: Path, rules: list[dict]) -> list[dict]:
        """규칙들을 한 번에 실행하고 매치마다 {rule_id, path, start_line, end_line, text, vars} 반환."""
        if not self.available:
            raise ToolError("capability_unavailable", "ast-grep 이 없음", recovery="npm i -g @ast-grep/cli")
        if not rules:
            return []
        docs = []
        for r in rules:
            doc: dict[str, Any] = {"id": r["id"], "language": r["language"], "rule": r["rule"]}
            if "constraints" in r:
                doc["constraints"] = r["constraints"]
            docs.append(doc)
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as fh:
            yaml.safe_dump_all(docs, fh, allow_unicode=True)
            rule_path = fh.name
        try:
            proc = subprocess.run(
                [self.settings.astgrep_bin, "scan", "--rule", rule_path, "--json=stream", "."],
                cwd=repo, capture_output=True, timeout=1800,
            )
        finally:
            Path(rule_path).unlink(missing_ok=True)
        if proc.returncode not in (0, 1):  # 1 = 매치 있음(error severity) 인 경우도 있음
            raise ToolError("engine_error", f"ast-grep 실패: {proc.stderr.decode('utf-8', 'replace')[-800:]}")
        matches = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            m = json.loads(line)
            single = m.get("metaVariables", {}).get("single", {})
            multi = m.get("metaVariables", {}).get("multi", {})
            matches.append({
                "rule_id": m["ruleId"],
                "path": Path(m["file"]).as_posix().removeprefix("./"),
                "start_line": m["range"]["start"]["line"] + 1,
                "end_line": m["range"]["end"]["line"] + 1,
                "text": m["text"],
                "vars": {k: {"text": v["text"], "line": v["range"]["start"]["line"] + 1} for k, v in single.items()},
                "multi": {k: " ".join(x.get("text", "") for x in v if x.get("text", "").strip(",")) for k, v in multi.items()},
            })
        return matches


def is_literal(text: str) -> bool:
    return text.strip().startswith(STRING_LITERAL_PREFIXES)
