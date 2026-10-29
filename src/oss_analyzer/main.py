"""MCP 서버 진입점 — modules/ 의 모든 파일을 import 해서 도구를 등록한다.

새 도구 묶음 추가: modules/ 에 파일을 만들고 `from oss_analyzer.server import mcp` 후 @mcp.tool() 로 정의.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys

import oss_analyzer.modules as modules_pkg
from oss_analyzer.server import mcp


def register_all() -> list[str]:
    loaded = []
    for _, name, _ in pkgutil.iter_modules(modules_pkg.__path__):
        try:
            importlib.import_module(f"oss_analyzer.modules.{name}")
            loaded.append(name)
        except Exception as e:  # noqa: BLE001 — 한 모듈 실패가 서버 전체를 막지 않도록
            print(f"  FAIL [{name}]: {e}", file=sys.stderr)
    return loaded


def main() -> None:
    register_all()
    mcp.run()


if __name__ == "__main__":
    main()
