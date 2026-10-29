"""표준 응답 envelope — 모든 도구가 같은 스키마로 응답한다.

{
  "ok": bool,
  "data": <도구별 JSON>,
  "warnings": [{"code", "message"}],
  "errors":   [{"code", "message", "recovery"}],
  "meta": {"collected_at", "duration_ms", "source"}
}

표준 에러 코드:
- invalid_request         : 인자 오류
- not_found               : 프로젝트·단위·근거 등 대상 없음
- capability_unavailable  : 필요한 외부 엔진이 없거나 해당 언어를 지원하지 않음
- evidence_rejected       : 근거 위치 검증 실패 (파일·줄·스니펫 불일치, 비밀 파일)
- lease_conflict          : 다른 에이전트가 임대 중인 단위
- revision_conflict       : 미확인 레코드의 revision 이 바뀜 → 다시 읽고 재시도
- gate_failed             : 완료 조건 미충족 (누락 심볼 등)
- engine_error            : 외부 엔진 실행 실패
- internal                : 그 외
"""
from __future__ import annotations

import functools
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable


class ToolError(Exception):
    """도구 실행 중 예상된 실패 — envelope 의 errors 로 변환된다."""

    def __init__(self, code: str, message: str, recovery: str | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery
        self.data = data


class Envelope:
    def __init__(self, source: str) -> None:
        self.source = source
        self._started = perf_counter()
        self._warnings: list[dict] = []

    def warn(self, code: str, message: str) -> None:
        self._warnings.append({"code": code, "message": message})

    def _meta(self) -> dict:
        return {
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration_ms": int((perf_counter() - self._started) * 1000),
            "source": self.source,
        }

    def ok(self, data: Any) -> dict:
        warnings = list(self._warnings)
        if isinstance(data, dict) and "_warnings" in data:
            warnings.extend(data.pop("_warnings"))
        return {"ok": True, "data": data, "warnings": warnings, "errors": [], "meta": self._meta()}

    def fail(self, code: str, message: str, recovery: str | None = None, data: Any = None) -> dict:
        error = {"code": code, "message": message}
        if recovery:
            error["recovery"] = recovery
        return {"ok": False, "data": data, "warnings": self._warnings, "errors": [error], "meta": self._meta()}


def enveloped(source: str) -> Callable:
    """core 함수를 envelope 응답으로 감싸는 데코레이터 (MCP 도구용)."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict:
            env = Envelope(source)
            try:
                return env.ok(fn(*args, **kwargs))
            except ToolError as e:
                return env.fail(e.code, e.message, e.recovery, e.data)
            except Exception as e:  # noqa: BLE001 — 도구 경계에서 모든 예외를 응답으로 변환
                return env.fail("internal", f"{type(e).__name__}: {e}")

        return wrapper

    return decorator
