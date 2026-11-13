"""화면 케이스 실행 — 페이지를 보이지 않는 브라우저 엔진에 띄워 확인하고 스크린샷을 남긴다 (app/shot.py 를 한 번 띄워 여러 장).

케이스: {"name": "home", "description": "첫 화면", "screen": {"path": "/"} 또는 {"file": "index.html"},
         "expect": {"visible": ["주문하기"], "selectors": ["form button"], "no_errors": true}}
선택: screen.width·height (기본 1280×800), screen.mobile=true (390×844), screen.wait_ms.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

from oss_analyzer.common.response import ToolError

MOBILE = (390, 844)
DESKTOP = (1280, 800)


def available() -> bool:
    return importlib.util.find_spec("PySide6") is not None and importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None


def page_spec(spec: dict, url: str | None, root, out: str, file_base: str | None = None) -> dict:
    """file_base: 대상 폴더를 내보내는 로컬 주소 (serve_folder) — 파일 화면은 file:// 이 아니라 이 주소로 연다."""
    w, h = MOBILE if spec.get("mobile") else DESKTOP
    if spec.get("file"):
        full = (root / spec["file"]).resolve()
        if not str(full).startswith(str(root.resolve())) or not full.is_file():
            raise ToolError("invalid_request", f"screen.file 은 대상 폴더 안의 파일이어야 함: {spec['file']}")
        rel = full.relative_to(root.resolve()).as_posix()
        target = f"{file_base}/{rel}" if file_base else str(full)
    elif url is not None:
        target = url.rstrip("/") + (spec.get("path") or "/")
    else:
        raise ToolError("invalid_request", "화면 케이스에는 screen.file(로컬 파일) 또는 screen.path + 서버 주소(*_url)가 필요함")
    return {"url": target, "out": out, "width": int(spec.get("width") or w), "height": int(spec.get("height") or h),
            "wait_ms": int(spec.get("wait_ms") or 800)}


class serve_folder:
    """대상 폴더만 127.0.0.1 로 내보낸다 — 페이지가 file:// 로 디스크의 다른 파일을 읽지 못하게."""

    def __init__(self, root):
        import functools
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def guess_type(self, path):  # 글자 파일은 UTF-8 이라고 알려 준다 (meta charset 이 없는 페이지의 한글이 깨지지 않게)
                t = super().guess_type(path)
                if t.startswith("text/") or t in ("application/javascript", "application/json", "image/svg+xml"):
                    t += "; charset=utf-8"
                return t

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def __enter__(self):
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()


def capture(pages: list[dict], timeout: float = 300) -> list[dict]:
    """여러 페이지를 브라우저 엔진 하나로 차례대로. 결과는 pages 와 같은 순서."""
    if not pages:
        return []
    if not available():
        raise ToolError("capability_unavailable", "화면 케이스에는 앱 구성요소가 필요함",
                        recovery="pip install 'beom-oss-analyzer[app]' (PySide6 WebEngine)")
    # 결과는 UTF-8 로 받는다 — Windows 기본 코드 페이지(cp949)로 나오면 한글 확인 결과가 깨진다
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu",
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run([sys.executable, "-m", "oss_analyzer.app.shot", json.dumps({"pages": pages})],
                          capture_output=True, timeout=timeout, env=env)
    for line in reversed(proc.stdout.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                got = json.loads(line)
            except ValueError:
                continue
            res = got.get("pages") or []
            return res + [{"ok": False, "errors": ["결과 없음"]}] * (len(pages) - len(res))
    tail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
    raise ToolError("internal", f"화면 확인 실패 (exit {proc.returncode}): {tail}")


def to_result(r: dict) -> dict:
    """케이스 결과 모양으로 — status 는 열림(200)/못 염(None), body 는 확인 결과, screenshot 은 이미지 경로."""
    loaded = r.get("ok") and "페이지를 열지 못함" not in (r.get("errors") or [])
    return {"status": 200 if loaded else None,
            "body": {"title": r.get("title"), "visible": r.get("found", []), "missing": r.get("missing", []),
                     "selectors_missing": r.get("selectors_missing", []), "errors": (r.get("errors") or [])[:5]},
            "screenshot": r.get("out") if loaded else None,
            **({} if loaded else {"error": "; ".join(r.get("errors") or ["페이지를 열지 못함"])[:300]})}
