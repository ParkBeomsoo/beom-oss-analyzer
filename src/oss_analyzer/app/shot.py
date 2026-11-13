"""화면 확인 — 페이지를 보이지 않는 창(Qt WebEngine)에 띄워 글자·요소가 있는지 보고 스크린샷을 남긴다.

    python -m oss_analyzer.app.shot '{"pages": [{"url": "...", "out": "a.png", "width": 1280, "height": 800,
                                                  "visible": ["주문하기"], "selectors": ["form button"], "wait_ms": 800}]}'

여러 페이지를 브라우저 엔진 하나로 차례대로 연다 (엔진 시작이 가장 오래 걸린다). 결과는 표준 출력 마지막 줄 JSON:
{"pages": [{"ok", "title", "found", "missing", "selectors_missing", "errors", "out", "height"}]}.
따로 설치할 것 없이 앱 구성요소(PySide6 WebEngine)만 쓴다.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    spec = json.loads(sys.argv[1])
    pages = spec.get("pages") or [spec]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    results: list[dict] = []
    state = {"i": -1, "cur": None, "timer": None}

    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802 — Qt 이름
            if state["cur"] is not None and level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                state["cur"]["errors"].append(str(message)[:300])

    view = QWebEngineView()
    view.setPage(Page(view))
    view.show()

    def next_page():
        if state["cur"] is not None:
            results.append({k: v for k, v in state["cur"].items() if not k.startswith("_")})
        state["i"] += 1
        if state["i"] >= len(pages):
            app.quit()
            return
        p = pages[state["i"]]
        state["cur"] = {"ok": False, "found": [], "missing": [], "selectors_missing": [], "errors": [], "out": p.get("out")}
        view.resize(int(p.get("width") or 1280), int(p.get("height") or 800))
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(lambda: (state["cur"]["errors"].append("시간 초과"), next_page()))
        guard.start(int(p.get("timeout_ms") or 30000))
        state["timer"] = guard
        url = p["url"]
        view.load(QUrl(url) if "://" in url else QUrl.fromLocalFile(url))

    def checked(raw, idx):
        if idx != state["i"]:  # 늦게 온 결과 (시간 초과로 이미 다음 페이지) — 버린다
            return
        p, cur = pages[state["i"]], state["cur"]
        if cur.get("_checked"):
            return
        cur["_checked"] = True
        try:
            res = json.loads(raw) if isinstance(raw, str) else {}
        except ValueError:
            res = {}
        cur.update(ok=True, title=res.get("title"), found=res.get("found", []), missing=res.get("missing", []),
                   selectors_missing=res.get("smissing", []))
        w, h = int(p.get("width") or 1280), int(p.get("height") or 800)
        full = min(max(h, int(res.get("height") or h)), 6000) if p.get("full_page", True) else h
        view.resize(w, full)

        def shoot():
            if idx != state["i"]:
                return
            if p.get("out"):
                view.grab().save(p["out"])
            cur["height"] = full
            if state["timer"]:
                state["timer"].stop()
            next_page()

        QTimer.singleShot(300, shoot)

    def loaded(ok):
        if state["cur"] is None or state["i"] >= len(pages) or state["cur"].get("_loaded"):
            return  # 페이지가 스스로 다시 이동해도 한 번만 확인한다
        state["cur"]["_loaded"] = True
        if not ok:
            state["cur"]["errors"].append("페이지를 열지 못함")
            if state["timer"]:
                state["timer"].stop()
            QTimer.singleShot(0, next_page)
            return
        p = pages[state["i"]]
        js = """(() => { const t = document.body ? document.body.innerText : ""; const want = %s, sel = %s;
          return JSON.stringify({ title: document.title, height: document.documentElement.scrollHeight,
                   found: want.filter((w) => t.includes(w)), missing: want.filter((w) => !t.includes(w)),
                   smissing: sel.filter((s) => { try { return !document.querySelector(s); } catch (e) { return true; } }) }); })()""" % (
            json.dumps(p.get("visible") or [], ensure_ascii=False), json.dumps(p.get("selectors") or [], ensure_ascii=False))
        idx = state["i"]
        QTimer.singleShot(int(p.get("wait_ms") or 800), lambda: idx == state["i"] and view.page().runJavaScript(js, 0, lambda r: checked(r, idx)))

    view.loadFinished.connect(loaded)
    QTimer.singleShot(0, next_page)
    app.exec()
    print(json.dumps({"pages": results}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
